"""AI services for Website Quality Checker."""

import importlib.util
import os
import threading
import tomllib
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from ai_review import (
    AIReviewError,
    DEFAULT_MODEL,
    prepare_review,
    review_key,
    generate_review,
)

from deep_review import (
    prepare_page_audit,
    prepare_screenshots,
    generate_page_audit,
)

from lintel_chat import answer_question


ROOT = Path(__file__).resolve().parent

load_dotenv(ROOT / ".env")

AI_LOCK = threading.Lock()


# --------------------------------------------------
# API CONFIGURATION
# --------------------------------------------------

def configuration():
    entries = {}

    path = ROOT / ".streamlit" / "secrets.toml"

    if path.is_file():
        try:
            with path.open("rb") as handle:
                entries = tomllib.load(handle)
        except (OSError, ValueError):
            raise AIReviewError(
                "The local secrets file could not be read."
            ) from None

    # Support Streamlit Community Cloud secrets.
    try:
        import streamlit as st
        cloud_key = st.secrets.get("OPENAI_API_KEY", "")
        cloud_model = st.secrets.get("OPENAI_MODEL", "")
    except (FileNotFoundError, KeyError):
        cloud_key = ""
        cloud_model = ""

    key = str(
        os.environ.get("OPENAI_API_KEY")
        or entries.get("OPENAI_API_KEY")
        or cloud_key
        or ""
    ).strip()

    model = str(
        os.environ.get("OPENAI_MODEL")
        or entries.get("OPENAI_MODEL")
        or cloud_model
        or DEFAULT_MODEL
    ).strip()

    return key, model


# --------------------------------------------------
# OPENAI CLIENT
# --------------------------------------------------

def get_client():
    key, _ = configuration()

    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY has not been configured."
        )

    return OpenAI(api_key=key)


# --------------------------------------------------
# GENERAL AI FUNCTION
# FIXES THE IMPORT ERROR
# --------------------------------------------------

def ask_ai(prompt):
    client = get_client()

    _, model = configuration()

    response = client.responses.create(
        model=model,
        input=prompt,
        max_output_tokens=500,
    )

    return response.output_text


# --------------------------------------------------
# AI STATUS
# --------------------------------------------------

def status():
    try:
        key, model = configuration()

        return {
            "configured": bool(key),
            "model": model,
            "sdk_available": (
                importlib.util.find_spec("openai") is not None
            ),
        }

    except AIReviewError as exc:
        return {
            "configured": False,
            "model": DEFAULT_MODEL,
            "sdk_available": False,
            "configuration_error": str(exc),
        }


# --------------------------------------------------
# SAVED SCANS
# --------------------------------------------------

def selected_scan(store, scan_id):
    if not isinstance(scan_id, str) or len(scan_id) > 100:
        raise ValueError("Select a saved scan first.")

    scan = store.scan(scan_id)

    if not scan:
        raise ValueError(
            "The selected saved scan was not found."
        )

    return scan


def indexed(store, scan, kind):
    keys = store.setting(
        "ai-" + kind + "-index-" + scan["id"],
        [],
    )

    return [
        result
        for key in keys[-30:]
        if isinstance(key, str)
        and key.startswith("ai-review-")
        and (result := store.setting(key))
    ]


# --------------------------------------------------
# AI OVERVIEW
# --------------------------------------------------

def overview(store, scan_id):
    scan = selected_scan(store, scan_id)

    _, model = configuration()

    payload = prepare_review(scan)

    return {
        "summary": store.setting(
            review_key(scan, payload, model)
        ),
        "deep_reviews": indexed(store, scan, "deep"),
        "issue_reviews": indexed(store, scan, "issue"),
        "status": status(),
    }


# --------------------------------------------------
# SAVE AI RESULTS
# --------------------------------------------------

def save_result(store, scan, key, result, kind):
    store.set_setting(key, result)

    index_key = (
        "ai-" + kind + "-index-" + scan["id"]
    )

    keys = store.setting(index_key, [])

    store.set_setting(
        index_key,
        (
            [item for item in keys if item != key]
            + [key]
        )[-30:],
    )


# --------------------------------------------------
# AI ANALYSIS AND CHATBOT
# --------------------------------------------------

def perform(store, action, data):

    if (
        not isinstance(data, dict)
        or data.get("consent") is not True
    ):
        raise ValueError(
            "Allow the selected scan context to be sent "
            "to OpenAI before requesting AI help."
        )

    scan = selected_scan(
        store,
        data.get("scan_id"),
    )

    if not AI_LOCK.acquire(blocking=False):
        raise AIReviewError(
            "Another AI request is running."
        )

    try:
        key, model = configuration()

        # CHATBOT
        if action == "chat":

            history = data.get("history", [])

            if (
                not isinstance(history, list)
                or len(history) > 12
                or any(
                    not isinstance(item, dict)
                    or item.get("role")
                    not in ("user", "assistant")
                    or not isinstance(
                        item.get("content"), str
                    )
                    or len(item["content"]) > 10000
                    for item in history
                )
            ):
                raise ValueError(
                    "The chatbot history was not valid."
                )

            result = answer_question(
                scan,
                data.get("question"),
                key,
                model,
                history,
                indexed(store, scan, "deep")[-4:],
            )

            return {
                "result": result,
                "cached": False,
            }

        # WEBSITE SUMMARY
        if action == "summary":

            payload = prepare_review(scan)

            cache_key = review_key(
                scan,
                payload,
                model,
            )

            cached = store.setting(cache_key)

            if cached:
                return {
                    "result": cached,
                    "cached": True,
                }

            result = generate_review(
                payload,
                key,
                model,
            )

            store.set_setting(
                cache_key,
                result,
            )

        # INDIVIDUAL ISSUE ANALYSIS
        elif action == "issue":

            issue = next(
                (
                    item
                    for item in scan["issues"]
                    if item["id"]
                    == data.get("issue_id")
                ),
                None,
            )

            if not issue:
                raise ValueError(
                    "This finding is not part "
                    "of the selected scan."
                )

            payload = prepare_review(
                scan,
                issue,
            )

            cache_key = review_key(
                scan,
                payload,
                model,
            )

            cached = store.setting(cache_key)

            if cached:
                return {
                    "result": cached,
                    "cached": True,
                }

            result = {
                **generate_review(
                    payload,
                    key,
                    model,
                ),
                "issue_id": issue["id"],
            }

            save_result(
                store,
                scan,
                cache_key,
                result,
                "issue",
            )

        # DEEP PAGE AUDIT
        elif action == "page":

            page = next(
                (
                    item
                    for item in scan["pages"]
                    if item["url"]
                    == data.get("page")
                ),
                None,
            )

            if not page:
                raise ValueError(
                    "Choose a page from "
                    "the selected saved scan."
                )

            include_images = data.get(
                "include_images",
                False,
            )

            if not isinstance(
                include_images,
                bool,
            ):
                raise ValueError(
                    "Choose whether to include screenshots."
                )

            images = (
                prepare_screenshots(
                    page,
                    store.artifacts,
                )
                if include_images
                else []
            )

            payload = prepare_page_audit(
                scan,
                page,
                images,
            )

            cache_key = review_key(
                scan,
                payload,
                model,
            )

            cached = store.setting(cache_key)

            if cached:
                return {
                    "result": cached,
                    "cached": True,
                }

            result = {
                **generate_page_audit(
                    payload,
                    key,
                    model,
                    images,
                ),
                "include_images": include_images,
                "requested_model": model,
            }

            save_result(
                store,
                scan,
                cache_key,
                result,
                "deep",
            )

        else:
            raise ValueError(
                "Unknown AI action."
            )

        return {
            "result": result,
            "cached": False,
        }

    finally:
        AI_LOCK.release()