# Website Quality Checker

A Python website review workspace with the supplied geometric Lintel interface. Python checks work without an API key. Optional AI page reviews and a small Ask Lintel side panel add context to saved scan evidence.

To update an existing installation, copy the project files into your existing project folder and keep its `data/` folder. Start this custom interface with `python server.py`.

## Start on your Mac

Install Python 3.11 or newer, unzip this project, then open Terminal in the project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

Open **http://localhost:8501** in your browser. Keep Terminal open while using it. Enter a public URL to create a real report. Existing saved scans remain available under History. The redesigned interface does not generate fabricated results or scores.

For desktop/mobile screenshots and browser checks, install Chromium once:

```bash
python -m playwright install chromium
```

On Linux, browser OS dependencies may also be required: `python -m playwright install --with-deps chromium`.

## Use it

1. Enter a public website URL and confirm permission to review it.
2. Choose a page limit and checks. Start with one page; larger scans take longer.
3. Open **Findings** to review evidence and suggested actions. Set owners, deadlines, launch blockers and intentional exceptions.
4. Open **Pages & Previews** for previews, connections, social metadata, resources and guided manual reviews.
5. Use **Compare scans** from History after saving a second scan. Disappearing issues are labeled "not seen", not automatically "fixed".
6. Export a PDF, CSV or archive under **Reports**. The archive includes checklists, screenshots and issue cards.

## Optional AI reviews and chat

Create a `.streamlit` folder in the project folder, then copy `secrets.toml.example` into it as `secrets.toml`. Set your key in that file:

```toml
OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
OPENAI_MODEL = "gpt-4.1-mini"
```

These values can instead be set as server environment variables. Restart `python server.py` after changing them. The custom server reads the local secrets file; launching Streamlit is not required. The key stays on the server and is excluded by `.gitignore`.

After a Python scan, open **AI Review**. Select scanned pages and optionally include their saved desktop/mobile screenshots. Allow the selected evidence to be sent to OpenAI, then run a deep review. Python checks the returned structure and matches textual quotations to supplied sources, dropping unsupported or duplicate suggestions. Visual interpretations and all AI suggestions still require review. They remain separate from the scanner's findings and result counts.

Use **Summarize Python Findings** for an explanation of the existing results, or **Explain with AI** in a finding's detail. The **Ask Lintel** button opens a compact panel on the right. It answers questions using the selected scan, limited relevant excerpts and recent saved AI reviews. Chat history stays in browser memory for the current session; AI reviews are saved with the scan's workspace data. Use **Download AI Reviews** for a separate text export; the standard PDF/CSV exports retain Python findings.

AI runs only when explicitly requested after the corresponding consent checkbox is selected. Your OpenAI API account may be billed separately. Each uncached page uses one request with up to 1,800 output tokens; summaries use 1,100, finding explanations 550 and chat replies 700. Repeating the same review with the same evidence and model reuses its saved result. Requests have a 45-second timeout, no automatic retries and no overlap. **Stop after this page** prevents the next page request.

Text payloads are bounded and common credentials, query strings, email addresses and key patterns are redacted. This is not full anonymization. Selected screenshots are sent as images and may contain visible personal information. Provider-side response storage is disabled (`store=False`); provider policies still apply. AI does not crawl additional pages, log in, submit forms or change the inspected website.

See **FEATURES.md** for the exact implementation and limits of every proposed feature. Some findings require manual review; the app does not certify a website as accessible, secure, compliant or ready to launch.

## Recurring scans

Save a schedule in the app, then run this in a second terminal with the same virtual environment and project directory:

```bash
python worker.py
```

The worker must remain running. Closing your laptop or shutting down the worker pauses scheduling. Notifications appear inside the workspace; no email, Slack or other outbound messaging is configured. Schedules are not created until you choose to save one. To process at most one due job and exit, run `python worker.py --once`.

## Storage and hosting

Scan history, reviews, schedules, page text and screenshots are stored in `data/`. Back up this folder if needed. Use `QUALITY_DATA_DIR` to select a persistent storage location. Do not commit `data/`, passwords or scanned customer content to GitHub.

The app binds to `127.0.0.1` by default. It is a private single-workspace tool, not a multi-tenant SaaS product. An optional `QUALITY_APP_PASSWORD` environment variable adds a shared password gate. It is not a substitute for a production authentication layer, rate limiting, TLS and per-user authorization. Anyone admitted to the same workspace can see its data.

The new branded interface runs from **server.py**, serving the bundled frontend and the existing scanner APIs. It requires a Python web host with persistent disk. The legacy Streamlit interface remains available with `python -m streamlit run app.py` for compatibility. Browser previews require Chromium; schedules require an always-on worker. No hosting deployment has been created for you.

Before publishing a public service, configure authentication and infrastructure resource controls, define retention/deletion and adapt the app's data/use notice to the actual operator and hosting setup. The package adds no billing system, analytics or advertising. Optional AI requests use OpenAI when configured and explicitly allowed.

## Scan boundaries

- Public HTTP/HTTPS pages on standard ports only. Private, loopback, reserved and metadata addresses are blocked. DNS results are pinned for HTTP connections and every redirect is checked.
- Source crawling follows same-origin, query-free links, with a maximum of 30 HTML pages. Link destinations and resources may be on other public hosts.
- Maximum 500 HTTP requests, 600 seconds for the HTTP transport, 5 MB per response and 50 MB total downloaded by the transport. Defaults are smaller. Browser rendering has additional per-page timeouts.
- robots.txt restrictions are respected. If robots.txt cannot be verified, the destination is left unverified.
- No account login, form submission, purchase, email, text message or phone call. Browser write requests, service workers, WebSockets and popup windows are blocked.
- Blocked requests, unavailable assets and scan limits produce partial coverage. JavaScript-heavy sites may render incompletely.
- Browser source and computed-style heuristics can have false positives. Contrast checks cover simple solid backgrounds; focus and form behavior need manual review.
- Timings are observations from the scanning machine. They are not Lighthouse scores or Core Web Vitals.
- The browser is not a sandbox for hostile sites. For public hosting, isolate the scanner and browser in restricted containers with external network controls.

## Developer checks

```bash
python -m pip install pytest
python -m pytest -q
```

Core modules: `network.py` (bounded transport), `scanner.py` (HTML checks), `browser.py` (optional rendering), `storage.py` (SQLite), `reports.py` (exports). `worker.py` handles schedules. Unit tests use offline fixtures; they do not contact real websites.

The optional custom-interface browser tests run when `LINTEL_TEST_BROWSER` points to an installed Chromium executable. They serve the real interface with fictional scans and simulated AI responses. `LINTEL_UI_CAPTURES` can point to a folder for screenshots.

Official implementation references: [Streamlit app testing](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest), [Playwright routing](https://playwright.dev/python/docs/api/class-route), [Playwright browser contexts](https://playwright.dev/python/docs/api/class-browser).

## Redesign / v2

The new interface uses locally bundled Open Sauce and Space Mono fonts, the four-color indigo/periwinkle/white/black palette, and original SVG cube geometry. Anime.js v4 implements scoped entrances, restrained press feedback, scan activity, result count reveals, filters, panels and confirmations. Reduced-motion preferences disable nonessential motion.

**Important:** the backend has no validated 0–100 quality scoring model. The interface preserves evidence-based result labels and displays verified destinations rather than inventing a score or severity rating.

The JavaScript bundle is included: **Node/npm are not required to run the app.** To edit and rebuild the supplied frontend source, run `npm install` followed by `npm run build` in the project folder. AI controls are a separate extension in `web/ai-ui.js` and `web/ai-ui.css`; they use the supplied typography and colors without replacing the original layout, progress animation or mobile navigation. Fonts and library licenses are in `web/assets/` and `THIRD_PARTY.md`.

On macOS, you can also launch through `Start.command`. If Terminal says it is not executable, run `chmod +x Start.command`, then double-click it. Browser installation remains a separate optional step.
