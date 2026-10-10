# Feature coverage

This package implements the proposed workspace, with automated checks, browser-assisted checks and explicitly guided reviews. It is a local application, not a deployed or independently certified service.

| Proposed feature | Included behavior | Boundary |
| --- | --- | --- |
| Broken links and downloads | HTTP checks with source page, target and status | Blocked, rate-limited and server failures are unverified, not automatically broken |
| Broken images | Image URL checks and browser load observations | Lazy/dynamic assets may be incomplete |
| Page titles and descriptions | Missing title/description and repeated titles | Recommendations are advisory |
| Alt text | Missing attribute detection, empty alt allowed | Quality and decorative purpose need review |
| Heading structure | Missing H1 and skipped levels | Does not enforce a blanket one-H1 rule |
| Navigation anchors | Check fragment targets in returned HTML | Dynamic targets need browser review |
| Large images | Configurable KB threshold | Size alone does not determine page performance |
| Form labels and button names | Source markup checks | Complex accessible-name computation needs manual review |
| Redirects, chains and loops | Hop recording, loop detection, direct-URL suggestions | Redirects may be intentional |
| Mobile overflow and touch targets | Browser measurements at 390 px | Size/spacing exceptions need review |
| Desktop/mobile previews | 1440 px / 390 px viewport screenshots | Not every device or full-page capture |
| Social sharing preview | Open Graph fields and image URL validation | Metadata card; not a pixel-exact social-platform preview |
| Placeholder text | Common unfinished wording and placeholder links | User must confirm intent |
| Spelling | Optional dictionary checks and allowed words | Supported selected languages, capped text sample |
| Text contrast | Computed foreground/background ratio for simple backgrounds | Complex images, transparency and semantics need manual review |
| Keyboard review | First 12 Tab stops, focus screenshot and manual checklist | Does not prove complete keyboard accessibility |
| Contact links | Basic email/phone syntax checks | Does not verify mailbox or number ownership |
| Search visibility | noindex and X-Robots-Tag detection; robots crawl rules | No search ranking or indexing guarantee |
| Mixed content | HTTP resource URLs on HTTPS pages | HTML and captured browser scope only |
| Performance breakdown | Resource sizes, request inventory, observed fetch timings | Not a Lighthouse/Core Web Vitals replacement |
| Visual change comparison | Matching screenshot pixel differences | Fonts, dynamic content and timing can cause changes |
| Shareable issue cards | Markdown download with evidence, owner and suggested action | Sharing is a user action; no public hosting of reports |
| Scan profiles | Business, portfolio, store, blog and custom | Presets adjust checks/thresholds, not platform-specific certification |
| Guided fix panel | Evidence, location, suggested action and page recheck | Does not edit the target website |
| Deep AI page review | Selected source text, metadata, forms, links and optional saved screenshots | Requires an API key and consent; Python validates structure and textual citations; AI suggestions require judgment |
| AI explanations | Saved scan summary and per-finding explanation | Explains existing Python findings without changing result counts |
| Ask Lintel side chat | Compact panel using the selected saved scan and recent AI reviews | One request per message; chat history stays in browser memory; no additional crawling or website edits |
| AI review export | Separate text download of saved AI page reviews and summary | Standard PDF/CSV exports contain Python findings |
| Before/after scan comparison | New, persistent and not-seen findings | Incomplete scans never prove a fix |
| Launch checklist | Saved manual checklist per page | User supplies judgments |
| Page overview | Page list, click depth, findings and resources | Limited to discovered and scanned pages |
| Scan history | Persistent SQLite and local artifacts | Requires persistent disk |
| Client-ready reports | Branded PDF, CSV and ZIP archive | Notes and checklists are included in the ZIP |
| Site map | Page selector, connection graph and filtered link table | Not a full site inventory or orphan-page detector |
| Duplicate content | High text similarity between scanned pages | Includes navigation/footer text; review-only |
| Missing-page test | Random nonexistent URL checks for HTTP 200 | SPA fallback may be intentional |
| Favicon checks | Declared icon detection and URL checks | Absence of a declaration does not prove no favicon exists |
| Form error/success review | Optional native validity inspection plus manual checklist | No automated filling/submission or server success verification |
| Popup obstruction | Large fixed/sticky overlay heuristic | Menus and banners can trigger intentional findings |
| Image layout | Missing dimensions and browser aspect-ratio heuristic | CSS and object-fit require interpretation |
| Reduced motion | Emulated preference, remaining animation observations, screenshot | Essential motion may remain intentionally |
| Print preview | Print-media screenshot and review checklist | Does not generate a print PDF of the target page |
| Multilingual checks | lang attribute and linked hreflang destination checks | No translation correctness assessment |
| Brand rules | Required phrases, outdated wording and custom dictionary | Required phrases apply to every scanned page |
| Issue assignments | Owner, due date, work status | Trusted workspace labels, no individual user accounts |
| Comments and evidence | Saved editable notes and image attachments | No threaded multi-user discussion or notifications to owners |
| Accepted exceptions | Intentional status requires a reason | Reused by stable issue identity |
| Launch blockers | User-selected blocker flag and open blocker count | App does not authorize deployment |
| Scheduled rescans | Daily/weekly schedules, separate worker, in-app notifications | Requires a running process; no email integration |
| Client handover | Reports and archive with issue states and notes | User exports and shares manually |
| Check this journey | Ordered URL reachability, link continuity and optional screenshots | Not automated user clicks, bookings or purchases |

Browser preview code is optional and reports missing dependencies honestly. Review VALIDATION.md for checks performed on the delivered package.
