# WebChecker AI update

Run the custom interface with `python server.py`, not `streamlit run app.py`.

Implemented or retained:
- Ask Lintel conversations survive navigation and page refreshes within the same browser tab via sessionStorage, separated by scan ID. Clear Chat deletes the current scan's history. Chat requests include up to 12 previous messages.
- Increased chat output allowance from 700 to 2400 tokens. A max-token empty response now displays a more useful diagnostic. This may increase API usage; response completeness depends on the model and context.
- Existing AI Review tab is preserved. The summary action is labeled ANALYZE WITH AI. Existing cached scan summary, issue explanation, and optional screenshot-based deep page review remain available.
- PDF exports append previously saved AI summary, issue explanations, and deep page suggestions. ZIP archives include `ai-reviews.json`. No new API request is triggered by export.
- AI findings remain clearly labeled as advisory and separate from verified Python scan results.

Secrets, virtual environments, private scan data and caches are excluded from this ZIP. Restore your own `.streamlit/secrets.toml` locally (never commit it). Run `python -m pip install -r requirements.txt` in your virtual environment.

Validation: Python compilation and JavaScript syntax passed. Full unit suite could not run in the packaging environment because the optional project dependencies are not installed. Verify AI calls with your own key and a saved scan.
