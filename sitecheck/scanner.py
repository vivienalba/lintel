from collections import deque, defaultdict
from dataclasses import asdict
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlsplit, urldefrag, unquote
import re
import uuid
from bs4 import BeautifulSoup
from .models import Options, Scan, now
from .network import SafeClient, ScanError, normalize, origin


def html_response(r):
    return "html" in r.headers.get("content-type", "").lower() or r.body.lstrip().lower().startswith((b"<!doctype html", b"<html"))


def status_label(r):
    if r.error or r.status in (401,403,408,429) or r.status >= 500:
        return "Couldn't verify"
    if 400 <= r.status < 500:
        return "Needs fixing"
    return "Working" if 200 <= r.status < 400 else "Couldn't verify"


def parse_page(scan, r, depth, options):
    soup = BeautifulSoup(r.text, "html.parser")
    page = r.url
    def add(category, title, evidence, fix, severity="Review", target="", code=""):
        if category in options.categories:
            scan.add(page=page, category=category, severity=severity, title=title, evidence=evidence, fix=fix, target=target, code=code)
    title = soup.title.get_text(" ",strip=True) if soup.title else ""
    meta = {}
    for tag in soup.find_all("meta"):
        key = str(tag.get("name",tag.get("property",""))).lower()
        meta[key] = tag.get("content", "")
    description = meta.get("description", "").strip()
    if not title:
        add("Metadata", "Missing page title", "The page has no nonempty title element.", "Add a descriptive, page-specific <title> in <head>.", "Needs fixing", code="missing-title")
    if not description:
        add("Metadata", "Missing meta description", "No nonempty description meta tag was found.", "Write a concise description of this page.", code="missing-description")
    if "viewport" not in meta:
        add("Technical", "Missing mobile viewport", "No viewport meta tag was found.", "Add a viewport meta tag using width=device-width and initial-scale=1.")
    robots = meta.get("robots", "") + " " + r.headers.get("x-robots-tag", "")
    if "noindex" in robots.lower() or re.search(r"\bnone\b",robots.lower()):
        add("Metadata", "Search indexing is disabled", robots.strip(), "Confirm this is intentional. Remove noindex only for pages that should appear in search.")
    social = {k:meta.get(k, "") for k in ["og:title","og:description","og:image","twitter:card"]}
    for key in ["og:title","og:description","og:image"]:
        if not social[key]:
            add("Metadata", "Missing social sharing field", key, "Add this Open Graph meta tag. Actual social platforms may cache previews differently.", target=key)
    lang = soup.html.get("lang", "") if soup.html else ""
    if not lang.strip():
        add("Accessibility", "Missing page language", "The HTML element has no lang value.", "Set the page language, for example <html lang=\"en\">.")
    headings = [{"level":int(h.name[1]),"text":h.get_text(" ",strip=True)} for h in soup.find_all(re.compile(r"^h[1-6]$"))]
    if not any(h["level"] == 1 for h in headings):
        add("Accessibility", "Main heading not found", "No H1 element was found.", "Review whether the page has a clear main heading.")
    for previous, current in zip(headings, headings[1:]):
        if current["level"] > previous["level"]+1:
            add("Accessibility", "Heading level skipped", current["text"][:160], "Review the heading hierarchy; nesting should explain the page structure.", target=current["text"][:160])
    ids = {str(t.get("id")) for t in soup.find_all(id=True)} | {str(t.get("name")) for t in soup.find_all("a",attrs={"name":True})}
    links, resources = [], []
    base_tag = soup.find("base", href=True)
    base = urljoin(page,base_tag["href"]) if base_tag else page
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        label = a.get_text(" ",strip=True) or a.get("aria-label","") or "Image / unnamed link"
        if href.startswith("mailto:"):
            address = unquote(href[7:].split("?")[0])
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",address):
                add("Links", "Review email link format", href, "Use a complete email address after mailto:. This does not verify mailbox ownership.", target=href)
            continue
        if href.startswith("tel:"):
            if len(re.sub(r"\D","",unquote(href[4:]))) < 5:
                add("Links", "Review telephone link format", href, "Use a dialable number after tel:, ideally including a country code.", target=href)
            continue
        if not href or href == "#":
            add("Content", "Placeholder link", label, "Replace the placeholder with a destination or use a button for an action.", target=label)
            continue
        if href.startswith(("javascript:","data:")):
            add("Links", "Nonstandard link destination", href[:160], "Review keyboard behavior and provide a real link destination where appropriate.", target=label)
            continue
        try:
            full = urljoin(base, href)
            target = normalize(full)
        except (ScanError,ValueError):
            continue
        links.append({"from":page, "to":target, "fragment":unquote(urlsplit(full).fragment),"label":label[:200],"kind":"page"})
    for index, img in enumerate(soup.find_all("img")):
        src = img.get("src") or img.get("data-src") or ""
        target = urljoin(base,src) if src else f"image-{index}"
        if not img.has_attr("alt"):
            add("Accessibility", "Image missing alt attribute", target, "Add meaningful alt text, or alt=\"\" for a purely decorative image.", target=target)
        if not img.get("width") or not img.get("height"):
            add("Images", "Image dimensions not declared in HTML", target, "Review whether CSS aspect-ratio or width and height reserve enough space before loading.", target=target)
        if src and not src.startswith("data:"):
            resources.append({"url":target,"kind":"image"})
        for entry in img.get("srcset","").split(","):
            candidate = entry.strip().split(" ")[0]
            if candidate and not candidate.startswith("data:"):
                resources.append({"url":urljoin(base,candidate),"kind":"image"})
    icons = soup.find_all("link",rel=lambda rel: rel and "icon" in str(rel).lower())
    if not icons:
        add("Metadata", "Favicon not explicitly declared", "No icon link tag was found.", "Declare a favicon. A browser may still discover /favicon.ico automatically.")
    for tag in soup.find_all(["script","link","iframe","source","video","audio"]):
        raw = tag.get("src") or tag.get("href")
        if not raw:
            continue
        kind = "resource"
        if tag.name == "link":
            rel = " ".join(tag.get("rel",[]))
            if "icon" in rel:
                kind = "favicon"
            elif "alternate" in rel and tag.get("hreflang"):
                kind = "translation"
            elif "stylesheet" not in rel:
                continue
        resources.append({"url":urljoin(base,raw),"kind":kind})
    if social.get("og:image"):
        resources.append({"url":urljoin(base,social["og:image"]),"kind":"social image"})
    for resource in resources:
        if page.startswith("https:") and resource["url"].startswith("http:"):
            add("Technical", "Insecure resource on HTTPS page", resource["url"], "Serve the resource over HTTPS and update its URL.", "Needs fixing", target=resource["url"])
    forms = []
    for index, form in enumerate(soup.find_all("form")):
        forms.append({"index":index,"method":form.get("method","get"),"action":urljoin(base,form.get("action",page)),"inputs":len(form.find_all(["input","select","textarea"]))})
    for index, control in enumerate(soup.find_all(["input","select","textarea"])):
        if control.get("type") in ("hidden","submit","button","reset","image"):
            continue
        cid = control.get("id","")
        labelledby = control.get("aria-labelledby", "").split()
        named = control.get("aria-label","").strip() or (labelledby and all(soup.find(id=i) for i in labelledby)) or control.find_parent("label") or (cid and soup.find("label", attrs={"for":cid}))
        if not named:
            target = cid or control.get("name") or f"field-{index}"
            add("Accessibility", "Form field may lack a label", target, "Associate a visible label with the field. A placeholder alone is not a label.", target=target)
    for index, button in enumerate(soup.find_all("button")):
        if not (button.get_text(" ",strip=True) or button.get("aria-label") or button.get("aria-labelledby") or button.find("img",alt=True)):
            add("Accessibility", "Button may lack an accessible name", f"Button {index+1}", "Add visible text or an accessible label describing the action.", target=f"button-{index}")
    for tag in soup(["script","style","noscript","template"]):
        tag.decompose()
    text = soup.get_text(" ",strip=True)
    for phrase in ("lorem ipsum","coming soon","your company name","insert text here"):
        if phrase in text.lower():
            add("Content", "Possible unfinished content", phrase, "Replace template text or confirm that this message is intentional.", target=phrase)
    for phrase in options.required_phrases:
        if phrase.strip() and phrase.casefold() not in text.casefold():
            add("Brand", "Required phrase not found", phrase, "Add this phrase or update your custom brand rule.", target=phrase)
    for phrase in options.forbidden_phrases:
        if phrase.strip() and phrase.casefold() in text.casefold():
            add("Brand", "Outdated or disallowed wording", phrase, "Replace the wording according to your brand rule.", target=phrase)
    if options.spelling and "Content" in options.categories:
        try:
            from spellchecker import SpellChecker
            spell = SpellChecker(language=options.language)
            spell.word_frequency.load_words(options.dictionary)
            words = re.findall(r"\b[a-zA-Z]{3,25}\b",text[:30000])
            unknown = sorted(spell.unknown(words))[:40]
            if unknown:
                add("Content", "Possible spelling mistakes", ", ".join(unknown), "Review these words. Add valid brand names and specialist terms to the custom dictionary.")
        except (ImportError,ValueError) as exc:
            scan.notes.append("Spelling check unavailable: "+str(exc))
    return {"url":page,"status":r.status,"title":title,"description":description,"lang":lang,"depth":depth,"headings":headings,"ids":sorted(ids),"text":text[:50000],"social":social,"forms":forms,"response_seconds":r.elapsed,"html_bytes":len(r.body),"truncated":r.truncated,"browser":{}}, links, resources


def unavailable_fix(error):
    detail=(error or "").lower()
    if "certificate" in detail or "ssl" in detail:
        return "Update the checker dependencies, check the computer’s date and time, and retry. If it persists, inspect the website’s TLS certificate. This is a connection failure, not proof of a website quality issue."
    if "robots.txt disallows" in detail:
        return "Review the website’s robots.txt rules. If you own the site, allow WebsiteQualityChecker on the intended public pages, then recheck."
    if "robots.txt" in detail:
        return "Open the displayed robots.txt URL in your browser and confirm it responds. Review the connection error or HTTP status shown above, then retry. The checker has not verified permission to crawl the site."
    return "Verify that the page is public and reachable, then recheck."


def run_scan(url, options=None, progress=None, client=None, artifact_dir=None):
    options = options or Options()
    url = normalize(url)
    options.max_pages = max(1,min(options.max_pages,30))
    options.max_requests = max(10,min(options.max_requests,500))
    options.max_seconds = max(10,min(options.max_seconds,600))
    scan = Scan(url,asdict(options))
    net = client or SafeClient(options.max_requests,options.max_seconds)
    work = getattr(progress, "work", lambda *args: None)
    work("pages", 0, options.max_pages)
    queue = deque([(url,0)])
    seen, resource_seen, by_url = set(),set(),{}
    while queue and len(scan.pages) < options.max_pages:
        current, depth = queue.popleft()
        if current in seen:
            continue
        work("pages", len(scan.pages), options.max_pages)
        seen.add(current)
        if progress:
            progress(f"Reading page {len(scan.pages)+1}: {current}")
        r = net.fetch(current)
        if status_label(r) != "Working":
            scan.complete = False
            scan.add(page=current,category="Technical",severity=status_label(r),title="Page could not be scanned",evidence=r.error or f"HTTP {r.status}",fix=unavailable_fix(r.error),code="page-unavailable")
            continue
        # Allow the starting redirect (e.g. example.com to www.example.com).
        if len(scan.pages) == 0:
            scan.url = r.url
        if origin(r.url) != origin(scan.url) or not html_response(r):
            scan.notes.append(f"Skipped non-HTML or off-site page: {r.url}")
            continue
        if r.url in by_url:
            continue
        if progress:
            progress('Checking page structure, metadata and accessibility: '+r.url)
        record, links, resources = parse_page(scan,r,depth,options)
        scan.pages.append(record)
        by_url[r.url] = record
        if r.truncated:
            scan.complete = False
            scan.notes.append(f"HTML body exceeded the size cap: {r.url}")
        for link in links:
            scan.links.append(link)
            if origin(link["to"]) == origin(scan.url) and not urlsplit(link["to"]).query and link["to"] not in seen:
                if len(queue) < 300:
                    queue.append((link["to"],depth+1))
        for resource in resources:
            key = (r.url,resource["url"])
            if key not in resource_seen:
                resource_seen.add(key)
                scan.resources.append({"page":r.url,**resource})
    work("pages", 1, 1)
    if queue:
        scan.complete = False
        scan.notes.append("Page limit reached. Findings cover the scanned pages, not the entire website.")
    resource_records = scan.resources if any(x in options.categories for x in ("Links","Images","Technical","Performance","Metadata")) else []
    link_records = scan.links if "Links" in options.categories else []
    destination_total = len(link_records)+len(resource_records)
    work("destinations", 0, max(1,destination_total))
    for idx, item in enumerate(link_records+resource_records):
        work("destinations", idx, max(1,destination_total))
        target = item.get("to",item.get("url"))
        page = item.get("from",item.get("page"))
        if progress and idx % 10 == 0:
            progress(f"Checking destination {idx+1} of {len(link_records)+len(resource_records)}")
        try:
            target = normalize(target)
        except (ScanError,ValueError):
            item["result"] = "Couldn't verify"
            continue
        r = net.fetch(target)
        result = status_label(r)
        item.update(status=r.status,result=result,final_url=r.url,redirects=r.chain,bytes=len(r.body),seconds=r.elapsed,truncated=r.truncated,error=r.error)
        category = "Images" if "image" in item["kind"] else "Links"
        if result != "Working":
            scan.complete = False
            scan.add(page=page,category=category,severity=result,title="Broken destination" if result=="Needs fixing" else "Destination could not be verified",evidence=r.error or f"HTTP {r.status}",fix="Update the URL if incorrect. For blocked or temporary failures, inspect it manually and retry later.",target=target,code="destination-status")
        if r.chain:
            scan.add(page=page,category="Links",severity="Review",title="Redirect chain" if len(r.chain)>1 else "Redirected link",evidence=f"{len(r.chain)} hop(s); final destination: {r.url}",fix="Consider linking directly to the final URL. A redirect may be intentional.",target=target,code="redirect")
        if "image" in item["kind"] and len(r.body) > options.image_kb*1024:
            scan.add(page=page,category="Images",severity="Review",title="Large image download",evidence=f"At least {len(r.body)/1024:.0f} KB; your threshold is {options.image_kb} KB.",fix="Resize and compress the image for its displayed dimensions.",target=target,code="large-image")
        if r.elapsed>2 and "Performance" in options.categories:
            scan.add(page=page,category="Performance",severity="Review",title="Slow response in this scan",evidence=f"{r.elapsed:.2f} seconds from this machine; not a real-user performance metric.",fix="Repeat from your audience's region and inspect server response and download size.",target=target)
        fragment = item.get("fragment")
        if fragment and result == "Working" and html_response(r):
            target_soup = BeautifulSoup(r.text,"html.parser")
            if not target_soup.find(id=fragment) and not target_soup.find("a",attrs={"name":fragment}):
                scan.add(page=page,category="Links",severity="Review",title="Anchor target not found in source HTML",evidence=f"#{fragment}",fix="Match the link fragment to a section ID. JavaScript may add the target later; verify in the browser.",target=target+"#"+fragment,code="anchor")
    work("destinations", 1, 1)
    work("content", 0, 1)
    if progress:
        progress('Comparing content and preparing technical findings')
    duplicates = defaultdict(list)
    for p in scan.pages:
        if p["title"]:
            duplicates[p["title"].casefold()].append(p["url"])
    if "Metadata" in options.categories:
        for title, pages in duplicates.items():
            if len(pages)>1:
                for page in pages:
                    scan.add(page=page,category="Metadata",severity="Review",title="Repeated page title",evidence=", ".join(pages),fix="Use a distinct title for each page's purpose.",code="duplicate-title")
    if "Content" in options.categories:
        for i,a in enumerate(scan.pages):
            for b in scan.pages[i+1:]:
                if min(len(a["text"]),len(b["text"]))>200:
                    similarity = SequenceMatcher(None,a["text"][:10000],b["text"][:10000],autojunk=True).ratio()
                    if similarity>.90:
                        scan.add(page=b["url"],category="Content",severity="Review",title="Very similar page text",evidence=f"{similarity:.0%} text similarity with {a['url']}; navigation and footers are included.",fix="Review whether this duplication is intentional. This is not a search ranking verdict.",target=a["url"])
    if "Technical" in options.categories and scan.pages:
        missing = net.fetch(origin(scan.url)+"/__quality_checker_missing_"+uuid.uuid4().hex)
        if missing.status == 200:
            scan.add(page=scan.url,category="Technical",severity="Review",title="Missing URL returned HTTP 200",evidence=missing.url,fix="Check your missing-page behavior. Client-rendered apps may use a 200 fallback intentionally.",code="soft-404")
        elif missing.error:
            scan.notes.append("Missing-page response test could not be completed: "+missing.error)
    previous = None
    for raw in options.journey[:10]:
        try:
            target = normalize(raw,scan.url)
        except (ScanError,ValueError) as exc:
            scan.journey.append({"url":raw,"result":"Couldn't verify","detail":str(exc)})
            continue
        if origin(target)!=origin(scan.url):
            scan.journey.append({"url":target,"result":"Couldn't verify","detail":"Journey steps must remain on this website."})
            continue
        r = net.fetch(target)
        linked = None
        if previous:
            linked = any(normalize(a["href"],previous.url)==target for a in BeautifulSoup(previous.text,"html.parser").find_all("a",href=True) if urlsplit(urljoin(previous.url,a["href"])).scheme in ("http","https"))
        scan.journey.append({"url":target,"result":status_label(r),"linked_from_previous":linked,"detail":r.error or f"HTTP {r.status}; URL reachability test, not a completed transaction."})
        previous = r
    work("content", 1, 1)
    if options.browser and scan.pages:
        work("browser", 0, 1)
        try:
            from .browser import inspect_browser
            inspect_browser(scan,options,net,artifact_dir,progress)
        except Exception as exc:
            scan.complete = False
            scan.notes.append("Browser checks could not finish: "+str(exc)[:300])
    else:
        scan.notes.append("Browser checks were not enabled. Layout, computed contrast, screenshots and interactive behavior were not tested.")
    if options.browser:
        work("browser", 1, 1)
    work("preparing", 0, 1)
    scan.finished = now()
    scan.notes.append(f"Read-only scan. {net.count} HTTP requests used. No login, purchase, form submission, email or phone call performed.")
    return scan
