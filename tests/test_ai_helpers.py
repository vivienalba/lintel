import ast
import copy
from html import escape
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import ai_review as ai
import deep_review as deep
import lintel_chat as chat


def sample_scan(count=2):
    pages=[{"url":f"https://example.test/page-{i}","title":"Book a visit", "description":"Choose a time for your visit.",
            "text":"Book a visit. Contact us for availability. No prices are listed on this page.",
            "headings":[{"level":1,"text":"Book a visit"}], "forms":[{"index":0,"method":"post","action":"/book?secret=omit","inputs":2}],
            "browser":{}, "truncated":False} for i in range(count)]
    issues=[{"id":f"i-{i}","page":pages[i%count]["url"],"title":"Missing social description","severity":"Review",
             "category":"Metadata","evidence":"No og:description tag found.","fix":"Add a social sharing description.","target":"og:description"} for i in range(count)]
    return {"id":"scan-123","url":"https://example.test/","pages":pages,"issues":issues,"links":[],"notes":[],"complete":True}


def suggestion(**overrides):
    item={"title":"Clarify availability","category":"Content","priority":"Medium","source_id":"page_text",
          "quote":"Contact us for availability.","reason":"This leaves the next step unclear.",
          "suggested_fix":"Explain when and how a visitor will hear back.","verification":"Review the copy and ask a user to describe the next step."}
    item.update(overrides)
    return item


def fake_sdk(text="Suggested action.", status="completed", failure=None):
    calls=[]
    class ProviderError(Exception):
        pass
    errors={name:type(name,(ProviderError,),{}) for name in ["AuthenticationError","RateLimitError","PermissionDeniedError","NotFoundError","BadRequestError","APITimeoutError","APIConnectionError"]}
    class Client:
        def __init__(self, **kwargs):
            calls.append(("client",kwargs))
            self.responses=self
        def __enter__(self):
            return self
        def __exit__(self,*args):
            return False
        def create(self, **kwargs):
            calls.append(("request",kwargs))
            if failure:
                raise errors[failure]("SECRET_PROVIDER_DETAIL sk-private-testkey")
            return SimpleNamespace(output_text=text,status=status,model="gpt-4.1-mini",
                                   usage=SimpleNamespace(input_tokens=125,output_tokens=45))
    return SimpleNamespace(OpenAI=Client,**errors),calls


class UnitTests(unittest.TestCase):
    def test_redaction_removes_credentials_queries_emails_and_keys(self):
        text=ai.compact_text("https://user:pass@example.test/x?token=secret#frag mail@domain.com sk-abcdefghijk password=private Bearer abc.def",1000)
        self.assertIn("https://example.test/x",text)
        for token in ["user:pass","token=secret","#frag","mail@domain.com","sk-abcdefghijk","private","abc.def"]:
            self.assertNotIn(token,text)

    def test_summary_prioritizes_errors_and_bounds_unicode(self):
        scan=sample_scan()
        scan["issues"]=[{**scan["issues"][0],"id":str(i),"evidence":"界"*5000,"severity":"Needs fixing" if i==79 else "Review"} for i in range(80)]
        payload=ai.prepare_review(scan)
        self.assertEqual(payload["findings"][0]["severity"],"Needs fixing")
        self.assertLessEqual(len(json.dumps(payload,ensure_ascii=False,separators=(",", ":")).encode()),ai.MAX_INPUT_BYTES)
        self.assertEqual(payload["included_findings"]+payload["omitted_findings"],80)

    def test_keys_change_when_evidence_or_model_changes(self):
        scan=sample_scan(); payload=ai.prepare_review(scan)
        first=ai.review_key(scan,payload)
        self.assertNotEqual(first,ai.review_key(scan,payload,"another-model"))
        payload["findings"][0]["evidence"]="Different finding"
        self.assertNotEqual(first,ai.review_key(scan,payload))

    def test_missing_key_fails_before_import_or_network(self):
        with self.assertRaisesRegex(ai.AIReviewError,"OPENAI_API_KEY"):
            ai.generate_review({},"")

    def test_missing_sdk_is_optional(self):
        with patch.dict(sys.modules,{"openai":None}):
            with self.assertRaisesRegex(ai.AIReviewError,"Install the AI dependency"):
                ai.generate_review({},"local-placeholder")

    def test_summary_request_controls(self):
        sdk,calls=fake_sdk()
        with patch.dict(sys.modules,{"openai":sdk}):
            result=ai.generate_review(ai.prepare_review(sample_scan()),"local-placeholder")
        config=calls[0][1]; req=calls[1][1]
        self.assertEqual(config["max_retries"],0); self.assertEqual(config["timeout"],45.0)
        self.assertEqual(config["base_url"],"https://api.openai.com/v1")
        self.assertFalse(req["store"]); self.assertEqual(req["max_output_tokens"],1100)
        self.assertNotIn("api_key",result); self.assertEqual(result["input_tokens"],125)

    def test_issue_cap(self):
        scan=sample_scan(); sdk,calls=fake_sdk()
        with patch.dict(sys.modules,{"openai":sdk}):
            ai.generate_review(ai.prepare_review(scan,scan["issues"][0]),"local-placeholder")
        self.assertEqual(calls[1][1]["max_output_tokens"],550)

    def test_api_failures_never_expose_provider_details(self):
        for error in ["AuthenticationError","RateLimitError","PermissionDeniedError","NotFoundError","BadRequestError","APITimeoutError","APIConnectionError"]:
            with self.subTest(error=error):
                sdk,calls=fake_sdk(failure=error)
                with patch.dict(sys.modules,{"openai":sdk}):
                    with self.assertRaises(ai.AIReviewError) as caught:
                        ai.generate_review(ai.prepare_review(sample_scan()),"local-placeholder")
                self.assertNotIn("SECRET_PROVIDER_DETAIL",str(caught.exception))
                self.assertEqual(len(calls),2)

    def test_empty_response_and_failed_status_are_errors(self):
        for text,status in [("","completed"),("partial","failed")]:
            sdk,_=fake_sdk(text,status)
            with patch.dict(sys.modules,{"openai":sdk}):
                with self.assertRaises(ai.AIReviewError):
                    ai.generate_review({},"local-placeholder")

    def test_partial_plain_response_marked_incomplete(self):
        sdk,_=fake_sdk("Partial explanation.","incomplete")
        with patch.dict(sys.modules,{"openai":sdk}):
            result=ai.generate_review({},"local-placeholder")
        self.assertFalse(result["complete"])

    def test_deep_payload_contains_source_but_excludes_team_data(self):
        scan=sample_scan();scan["reviews"]={"owner":"PRIVATE_OWNER"};scan["settings"]={"key":"PRIVATE_SETTING"}
        page=scan["pages"][0];page["private_attachment"]="PRIVATE_ATTACHMENT"
        payload=deep.prepare_page_audit(scan,page)
        encoded=json.dumps(payload)
        self.assertIn("Contact us for availability.",payload["sources"]["page_text"])
        self.assertIn("heading_0",payload["sources"]); self.assertIn("form_0",payload["sources"])
        for secret in ["PRIVATE_OWNER","PRIVATE_SETTING","PRIVATE_ATTACHMENT","secret=omit"]:
            self.assertNotIn(secret,encoded)

    def test_deep_unicode_and_many_sources_remain_bounded(self):
        scan=sample_scan();page=scan["pages"][0]
        page["text"]="界"*50000
        page["headings"]=[{"level":2,"text":"界"*500} for _ in range(100)]
        payload=deep.prepare_page_audit(scan,page)
        self.assertLessEqual(len(json.dumps(payload,ensure_ascii=False,separators=(",", ":")).encode()),ai.MAX_AUDIT_BYTES)
        self.assertTrue(payload["coverage"]["source_text_shortened"])
        self.assertTrue(payload["coverage"]["stored_text_may_be_truncated"])

    def test_deep_rejects_invented_citations_unknown_sources_and_duplicates(self):
        scan=sample_scan();payload=deep.prepare_page_audit(scan,scan["pages"][0])
        raw={"summary":"Review next steps.","findings":[suggestion(),suggestion(),suggestion(quote="This quote does not exist"),suggestion(source_id="unseen_page"),suggestion(title="Missing social description")],"manual_checks":["Check the form on a test environment."]}
        result=deep.validate_audit(raw,payload)
        self.assertEqual(len(result["findings"]),1);self.assertEqual(result["discarded_findings"],4)
        self.assertEqual(result["findings"][0]["page"],scan["pages"][0]["url"])

    def test_visual_citation_requires_an_actually_supplied_image(self):
        scan=sample_scan();payload=deep.prepare_page_audit(scan,scan["pages"][0])
        raw={"summary":"Visual inspection.","findings":[suggestion(source_id="screenshot_mobile",quote="The button is clipped.")],"manual_checks":[]}
        self.assertEqual(deep.validate_audit(raw,payload)["findings"],[])
        payload["screenshots"]=[{"source_id":"screenshot_mobile"}]
        finding=deep.validate_audit(raw,payload)["findings"][0]
        self.assertEqual(finding["evidence_status"],"Visual interpretation requires review")

    def test_screenshot_guard_rejects_outside_paths_and_corrupt_files(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"artifacts";root.mkdir()
            outside=Path(temp)/"outside.png";Image.new("RGB",(80,80),"white").save(outside)
            good=root/"desktop.png";Image.new("RGB",(1440,1000),"white").save(good)
            bad=root/"broken.png";bad.write_text("not a picture")
            page={"browser":{"screenshots":{"desktop":str(outside),"mobile":str(bad)}}}
            self.assertEqual(deep.prepare_screenshots(page,root),[])
            page["browser"]["screenshots"]["desktop"]=str(good)
            images=deep.prepare_screenshots(page,root)
            self.assertEqual(len(images),1);self.assertTrue(images[0]["image_url"].startswith("data:image/jpeg;base64,"))

    def test_deep_request_has_schema_cap_and_grounding(self):
        scan=sample_scan();payload=deep.prepare_page_audit(scan,scan["pages"][0])
        sdk,calls=fake_sdk(json.dumps({"summary":"Review wording.","findings":[suggestion()],"manual_checks":[]}))
        with patch.dict(sys.modules,{"openai":sdk}):
            result=deep.generate_page_audit(payload,"local-placeholder")
        request=calls[1][1]
        self.assertEqual(request["max_output_tokens"],1800)
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertEqual(request["text"]["format"]["type"],"json_schema")
        self.assertEqual(len(result["findings"]),1)

    def test_deep_invalid_and_incomplete_json_never_adds_findings(self):
        scan=sample_scan();payload=deep.prepare_page_audit(scan,scan["pages"][0])
        for text,status in [('not JSON','completed'),('{"summary":"cut off','incomplete'),('{}','completed')]:
            sdk,_=fake_sdk(text,status)
            with patch.dict(sys.modules,{"openai":sdk}):
                with self.assertRaises(ai.AIReviewError):
                    deep.generate_page_audit(payload,"local-placeholder")

    def test_no_source_fails_before_network(self):
        scan=sample_scan();scan["pages"][0]["text"]=""
        payload=deep.prepare_page_audit(scan,scan["pages"][0]);sdk,calls=fake_sdk()
        with patch.dict(sys.modules,{"openai":sdk}):
            with self.assertRaises(ai.AIReviewError):
                deep.generate_page_audit(payload,"local-placeholder")
        self.assertEqual(calls,[])

    def test_chat_context_bounded_and_original_data_unchanged(self):
        scan=sample_scan(30);snapshot=copy.deepcopy(scan)
        history=[{"role":"assistant","content":"界"*9000} for _ in range(12)]
        payload=chat.prepare_chat(scan,"What should I fix first?",history)
        self.assertLessEqual(len(json.dumps(payload,ensure_ascii=False,separators=(",", ":")).encode()),ai.MAX_INPUT_BYTES)
        self.assertEqual(payload["total_findings"],30);self.assertEqual(scan,snapshot)

    def test_chat_oversized_message_rejected_without_request(self):
        sdk,calls=fake_sdk()
        with patch.dict(sys.modules,{"openai":sdk}):
            with self.assertRaises(ai.AIReviewError):
                chat.answer_question(sample_scan(),"x"*2001,"local-placeholder")
        self.assertEqual(calls,[])

    def test_chat_one_request_with_saved_review_context(self):
        scan=sample_scan();sdk,calls=fake_sdk("Fix the missing description first.")
        audit={"page":scan["url"],"summary":"CLARIFY_AVAILABLE_HOURS","findings":[]}
        with patch.dict(sys.modules,{"openai":sdk}):
            result=chat.answer_question(scan,"What should I fix?","local-placeholder",audits=[audit])
        self.assertEqual(len(calls),2);self.assertEqual(calls[1][1]["max_output_tokens"],2400)
        self.assertIn("CLARIFY_AVAILABLE_HOURS",calls[1][1]["input"])
        self.assertEqual(result["text"],"Fix the missing description first.")
