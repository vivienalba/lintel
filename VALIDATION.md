# Validation / supplied UI with AI additions

Verified on 9 October 2026 with Python 3.12 and headless Chromium.

- 77 automated tests passed, plus 7 subtests. The suite covers the existing scanner, storage, legacy Streamlit interface, HTTP API, AI helper modules and the supplied custom interface.
- The original stylesheet and all 9 bundled font/license/icon assets match the uploaded ZIP byte for byte. The AI controls use a separate matching extension. The supplied geometric layout, navigation and progress animation are retained.
- Desktop checks at 1440 × 1000 and mobile checks at 390 × 844 covered landing, saved reports, AI observations, summary, finding explanation, sidebar chat and original mobile navigation. Neither view overflowed the document horizontally. The chat panel stayed within the mobile viewport. No JavaScript page errors were observed.
- AI consent gates, one request per chat message, saved review reuse, stop after the current page, keyboard Escape and preserved checkbox focus were verified in the browser. Returned markup was displayed as text rather than executed.
- Backend checks covered missing consent, password and origin guards, scan/page/finding identity, invalid chat history, request overlap, failure recovery, local secrets setup and keeping keys out of browser responses.
- Helper checks covered bounded Unicode payloads, basic redaction, provider error messages, output limits, incomplete responses, structured review validation, matching source quotes, duplicate suggestions and screenshot path restrictions.

## Test boundaries

Tests used fictional offline website fixtures. Browser tests served the real Python API and supplied interface with simulated AI responses. No paid OpenAI request was made; live model behavior and API account permissions need your configured key. AI interpretation always requires judgment, including findings whose quotations match the supplied source.

Live third-party website audits, long-running hosted schedules and multi-user hosting were not tested. Browser-assisted website inspection requires installing Playwright Chromium. Test servers, populated databases and provider credentials are excluded from the delivered ZIP. Fresh installations contain no sample scan results.

Run `python -m pytest -q` after installing pytest and the project requirements. For the custom-interface browser tests, set `LINTEL_TEST_BROWSER` to an installed Chromium executable; those three tests otherwise skip. The app itself starts with `python server.py`.
