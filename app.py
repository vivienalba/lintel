"""Run: python -m streamlit run app.py"""
from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import hmac
import io
import json
import os
import zipfile
import streamlit as st
from PIL import Image
from sitecheck.models import Options
from sitecheck.network import normalize, ScanError
from sitecheck.scanner import run_scan
from sitecheck.storage import Store,compare_scans
from sitecheck.reports import csv_report,pdf_report,issue_markdown,visual_diff
from ai_service import ask_ai

st.set_page_config(page_title="Lintel",page_icon="◎",layout="wide",initial_sidebar_state="expanded")
st.markdown('''<style>
 .block-container{max-width:1240px;padding-top:2.5rem;padding-bottom:4rem}
 h1{letter-spacing:-.045em;font-weight:650!important;font-size:2.9rem!important;line-height:1.1!important}
 h2,h3{letter-spacing:-.025em}p{line-height:1.6}
 [data-testid="stSidebar"]{border-right:1px solid #dce1d8}
 .eyebrow{font-size:.72rem;font-weight:700;letter-spacing:.15em;text-transform:uppercase;color:#426858;margin-bottom:14px}
 .intro{max-width:660px;color:#617067;font-size:1.07rem;margin-top:12px;margin-bottom:24px}
 .note{border-left:3px solid #176653;padding:12px 18px;background:#edf2ec;color:#2c483b;margin:14px 0 24px}
 .rule{height:1px;background:#dce1d8;margin:24px 0}
 [data-testid="stMetricValue"]{font-size:2rem;font-weight:600}
 [data-testid="stMetric"]{border-bottom:2px solid #dce1d8;padding:12px 0 18px}
 button:focus-visible,a:focus-visible{outline:3px solid #176653!important;outline-offset:3px}
 @media(max-width:640px){h1{font-size:2.15rem!important}.block-container{padding:1.5rem 1rem}}
 @media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
 </style>''',unsafe_allow_html=True)

password=os.environ.get("QUALITY_APP_PASSWORD","")
if password and not st.session_state.get("authenticated"):
    st.title("Your website review workspace")
    with st.form("login"):
        entered=st.text_input("Workspace password",type="password")
        if st.form_submit_button("Open workspace"):
            if hmac.compare_digest(entered,password):
                st.session_state.authenticated=True
                st.rerun()
            else:
                st.error("The password did not match.")
    st.stop()

store=Store()
reviews=store.reviews()
history=store.scans()
with st.sidebar:
    st.markdown("### ◎ Website checker")
    st.caption("A clearer path to launch.")
    nav=st.radio("Workspace",["Scan","Issues","Pages","Compare","Reports","Schedules","Settings"],label_visibility="collapsed",key="navigation")
    st.divider()
    if history:
        chosen=st.selectbox("Saved scan",[h["id"] for h in history],format_func=lambda sid:next(h["created"][:16].replace("T"," ")+" / "+urlsplit(h["url"]).netloc for h in history if h["id"]==sid),key="chosen_scan")
        scan=store.scan(chosen)
        st.caption(f"{len(scan['pages'])} pages / {len(scan['issues'])} findings")
    else:
        scan=None
        st.caption("Private workspace / Optional AI analysis")
    st.divider()
    st.caption("Private workspace / No AI key required")
    with st.expander("Data & use"):
        st.write(
            "URLs, page text, findings, screenshots and notes are stored "
            "in this installation. When you choose AI analysis, relevant "
            "website findings are sent to the configured AI service to "
            "generate recommendations. AI analysis is optional."
        )
        st.write(
            "Reviews are advisory, not accessibility, security or legal certification. "
            "This workspace is for one owner or trusted team; it has no separate client accounts."
        )


def title(kicker,heading,body=""):
    st.markdown(f'<div class="eyebrow">{escape(kicker)}</div>',unsafe_allow_html=True)
    st.title(heading)
    if body:
        st.markdown(f'<div class="intro">{escape(body)}</div>',unsafe_allow_html=True)


def summary(data):
    cols=st.columns(4)
    cols[0].metric("Pages inspected",len(data["pages"]))
    for col,label in zip(cols[1:],["Needs fixing","Review","Couldn't verify"]):
        col.metric(label,sum(i["severity"]==label for i in data["issues"]))
    if not data.get("complete",False):
        st.info("Partial coverage: review the scan notes before drawing conclusions.")


def execute(url,options):
    message=st.empty()
    try:
        with st.spinner("Checking the website…"):
            result=run_scan(url,options,progress=message.info,artifact_dir=store.artifacts)
            data=store.save_scan(result)
        message.empty()
        st.session_state["latest_completed"]=data["id"]
        st.success(f"Scan saved: {len(data['pages'])} pages and {len(data['issues'])} findings. Open Issues to review.")
        # The latest scan becomes the default selector on the next rerun.
        st.session_state.pop("chosen_scan",None)
        st.rerun()
    except Exception as exc:
        message.empty()
        st.error("The scan could not finish: "+str(exc))


def image_path(path):
    if not path:
        return None
    path=Path(path).resolve()
    return path if path.is_relative_to(store.root) and path.is_file() else None


def require_scan():
    if not scan:
        st.info("Run a scan or load the sample from Scan to use this view.")
        st.stop()


def lines(value):
    return [s.strip() for s in value.splitlines() if s.strip()]

def analyze_scan_with_ai(data):
    issues = data.get("issues", [])

    if not issues:
        return "No findings were detected in this scan."

    # Limit what is sent to the API to control cost and data exposure.
    compact_issues = []

    for issue in issues[:50]:
        compact_issues.append({
            "severity": issue.get("severity"),
            "category": issue.get("category"),
            "title": issue.get("title"),
            "page": issue.get("page"),
            "evidence": issue.get("evidence"),
            "suggested_fix": issue.get("fix"),
        })

    prompt = f"""
You are assisting with a website quality audit.

Analyze the deterministic findings produced by Website Quality Checker.

Website:
{data.get("url", "")}

Findings:
{json.dumps(compact_issues, indent=2)}

Provide a concise professional analysis.

Use these sections:

PRIORITY
Identify the most important issues to address first.

WHY IT MATTERS
Explain the practical impact on users, accessibility, SEO,
performance, or website quality where supported by the findings.

RECOMMENDED NEXT STEPS
Give specific actions in priority order.

IMPORTANT:
Do not claim the website is compliant, secure, accessible,
or issue-free.
Do not invent problems that are not supported by the findings.
Clearly distinguish confirmed findings from recommendations.
"""

    return ask_ai(prompt)

if nav=="Scan":
    title("Check before you share","Good websites deserve\na final check.","Find broken links, overlooked content and layout issues. Review the evidence, keep track of fixes, and leave with a useful report.")
    with st.form("scan_form"):
        url=st.text_input("Website URL",placeholder="https://your-website.com")
        c1,c2,c3=st.columns(3)
        profile=c1.selectbox("Scan profile",["Business website","Portfolio","Online store","Blog","Custom"])
        limit=c2.selectbox("Pages to inspect",[1,5,10,20,30],index=1)
        browser=c3.checkbox("Include browser previews",help="Requires Chromium. Captures desktop, mobile, print, focus and reduced-motion previews.")
        with st.expander("Choose checks and limits"):
            categories=st.multiselect("Check categories",Options().categories,default=Options().categories)
            a,b,c=st.columns(3)
            requests=a.slider("Maximum requests",20,500,150,10)
            seconds=b.slider("Time budget (seconds)",30,600,180,30)
            image_kb=c.number_input("Large image threshold (KB)",100,10000,500,100)
            a,b,c=st.columns(3)
            browser_pages=a.slider("Pages with browser previews",1,5,2)
            spelling=b.checkbox("Check spelling")
            form_checks=c.checkbox("Inspect form validity states",help="Reads native browser validity. Does not fill or submit forms.")
            language=st.selectbox("Spelling language",["en","es","fr","de","pt","it"])
            st.caption("Blog and online-store profiles enable spelling; portfolio uses a tighter 350 KB image threshold. Explicit categories still control source checks.")
        with st.expander("Brand rules and page journey"):
            defaults=store.setting("brand_rules",{})
            dictionary=st.text_area("Allowed dictionary words (one per line)","\n".join(defaults.get("dictionary",[])))
            required=st.text_area("Phrases required on each page (one per line)","\n".join(defaults.get("required_phrases",[])))
            forbidden=st.text_area("Outdated wording to find (one per line)","\n".join(defaults.get("forbidden_phrases",[])))
            journey=st.text_area("Check this journey: URLs or paths in order",placeholder="/\n/services\n/booking",help="Checks URL reachability and links between steps. Browser mode adds previews. Does not click through transactions.")
        consent=st.checkbox("I own this website or have permission to review it.")
        submitted=st.form_submit_button("Scan website",type="primary",use_container_width=True)
    if submitted:
        if not consent:
            st.warning("Confirm permission before starting the scan.")
        elif not url.strip():
            st.warning("Enter the website URL.")
        else:
            options=Options(max_pages=limit,max_requests=requests,max_seconds=seconds,image_kb=min(image_kb,350) if profile=="Portfolio" else image_kb,browser=browser,browser_pages=browser_pages,spelling=spelling or profile in ("Blog","Online store"),language=language,profile=profile,dictionary=lines(dictionary),required_phrases=lines(required),forbidden_phrases=lines(forbidden),journey=lines(journey),form_checks=form_checks,categories=categories)
            execute(url,options)
    st.caption("Public HTTP/HTTPS pages only. Read only checks with page, request and time limits.")
    if st.button("Explore a sample report"):
        from sitecheck.demo import create_demo
        store.save_scan(create_demo())
        st.session_state.pop("chosen_scan",None)
        st.rerun()
    if scan:
        st.divider()
        st.subheader("Your latest selected scan")
        st.write(scan["url"])
        summary(scan)
        with st.expander("Scope and scan notes"):
            for note in scan["notes"]:
                st.write("• "+note)
            st.json(scan["options"],expanded=False)

elif nav=="Issues":
    title("Review & resolve","Small fixes. Better experience.","Each finding includes evidence and a suggested next step. Save ownership, deadlines and decisions as you work.")
    require_scan()
    summary(scan)
    st.subheader("AI analysis")
    st.caption(
        "Get a prioritized explanation of the findings from this scan. "
        "AI analysis is advisory and does not replace the underlying checks."
    )

    if st.button("Analyze findings with AI", type="primary"):
        try:
            with st.spinner("Analyzing findings…"):
                analysis = analyze_scan_with_ai(scan)

            st.session_state["ai_scan_analysis"] = {
                "scan_id": scan["id"],
                "analysis": analysis,
            }

        except Exception as exc:
            st.error("AI analysis could not be completed: " + str(exc))

    saved_ai = st.session_state.get("ai_scan_analysis")

    if saved_ai and saved_ai.get("scan_id") == scan["id"]:
        with st.container(border=True):
            st.markdown(saved_ai["analysis"])

    st.divider()
    a,b,c=st.columns(3)
    severity=a.multiselect("Result",["Needs fixing","Review","Couldn't verify"],default=["Needs fixing","Review","Couldn't verify"])
    category=b.multiselect("Category",sorted({i["category"] for i in scan["issues"]}))
    query=c.text_input("Find a page or issue")
    statuses=["Open","In progress","Fixed (manual)","Intentional","Needs review"]
    selected_status=st.multiselect("Work status",statuses)
    filtered=[i for i in scan["issues"] if i["severity"] in severity and (not category or i["category"] in category) and (not query or query.lower() in json.dumps(i).lower()) and (not selected_status or reviews.get(i["id"],{}).get("status","Open") in selected_status)]
    blockers=[i for i in scan["issues"] if reviews.get(i["id"],{}).get("blocker") and reviews.get(i["id"],{}).get("status") not in ("Fixed (manual)","Intentional")]
    if blockers:
        st.warning(f"{len(blockers)} open finding(s) marked as launch blockers by your team.")
    st.caption(f"{len(filtered)} findings shown")
    for issue in filtered[:100]:
        rid=issue["id"]
        review=reviews.get(rid,{})
        with st.expander(f"{issue['severity']} / {issue['title']} / {urlsplit(issue['page']).path}"):
            st.text(issue["page"])
            st.markdown("**What was found**")
            st.write(issue["evidence"])
            st.markdown("**Suggested action**")
            st.write(issue["fix"])
            if issue["target"]:
                st.code(issue["target"],language=None)
            with st.form("review-"+rid):
                a,b,c=st.columns(3)
                status=a.selectbox("Status",statuses,index=statuses.index(review.get("status","Open")))
                owner=b.text_input("Owner",review.get("owner",""))
                due=c.text_input("Due date",review.get("due",""),placeholder="YYYY-MM-DD")
                blocker=st.checkbox("Must resolve before launch",review.get("blocker",False))
                notes=st.text_area("Notes / reason for an exception",review.get("notes",""))
                evidence=st.file_uploader("Attach a screenshot",type=["png","jpg","jpeg"])
                if st.form_submit_button("Save review"):
                    payload={"status":status,"owner":owner,"due":due,"blocker":blocker,"notes":notes,"attachment":review.get("attachment","")}
                    if status=="Intentional" and not notes.strip():
                        st.warning("Add a reason for the intentional exception.")
                    else:
                        try:
                            if evidence:
                                img=Image.open(evidence);img.thumbnail((2400,2400))
                                path=store.artifacts/(rid+"-attachment.png")
                                img.convert("RGB").save(path)
                                payload["attachment"]=str(path)
                            store.review(rid,payload)
                            st.rerun()
                        except Exception as exc:
                            st.error("Could not save the attachment: "+str(exc))
            attachment=image_path(review.get("attachment"))
            if attachment:
                st.image(str(attachment),caption="Attached review evidence",width=600)
            a,b=st.columns(2)
            a.download_button("Download issue card",issue_markdown(issue,review),file_name=f"issue-{rid}.md",mime="text/markdown",key="card-"+rid)
            if b.button("Recheck this page",key="recheck-"+rid):
                settings={**scan["options"],"max_pages":1,"journey":[]}
                execute(issue["page"],Options(**settings))
    if not filtered:
        st.info("No findings match these filters. This does not certify that the website is issue-free.")

elif nav=="Pages":
    title("Inspect the details","Every page has a story.","Explore page connections, previews, resources and the checks that need your judgment.")
    require_scan()
    if not scan["pages"]:
        st.info("No HTML pages were available to inspect. Review Issues for the failure reason.")
        st.stop()
    page=st.selectbox("Page",scan["pages"],format_func=lambda p:p["url"])
    tab1,tab2,tab3,tab4=st.tabs(["Page & previews","Site map","Resources & speed","Manual review"])
    with tab1:
        st.subheader(page["title"] or "Untitled page")
        st.write(page["description"] or "No meta description")
        a,b,c=st.columns(3)
        a.metric("Click depth",page["depth"])
        b.metric("HTML download",f"{page['html_bytes']/1024:.0f} KB")
        c.metric("Fetch time",f"{page['response_seconds']:.2f} s")
        st.caption("Fetch time is a single server-side observation, not a page-speed score.")
        shots=page.get("browser",{}).get("screenshots",{})
        available={k:image_path(v) for k,v in shots.items() if image_path(v)}
        if available:
            mode=st.radio("Preview",list(available),horizontal=True)
            st.image(str(available[mode]),caption=mode,use_container_width=True)
            st.download_button("Download screenshot",available[mode].read_bytes(),available[mode].name,"image/png")
        else:
            st.info("No browser preview for this page. Enable browser previews and install Chromium to capture it.")
        for note in page.get("browser",{}).get("limitations",[]):
            st.caption(note)
        st.subheader("Social sharing fields")
        social=page["social"]
        with st.container(border=True):
            st.caption(urlsplit(page["url"]).netloc.upper())
            st.markdown("**"+(social.get("og:title") or page["title"] or "Missing title")+"**")
            st.write(social.get("og:description") or "Missing social description")
            st.text("Image: "+(social.get("og:image") or "Not set"))
        st.caption("Metadata preview only. Platforms may crop or cache content differently.")
        with st.expander("Headings, forms and browser observations"):
            st.json({"headings":page["headings"],"forms":page["forms"],"browser":page.get("browser",{})})
    with tab2:
        st.caption("Select a page above to explore its connections. Only links found in scanned HTML are shown.")
        import graphviz
        graph=graphviz.Digraph()
        graph.attr(rankdir="TB",bgcolor="transparent")
        urls={p["url"] for p in scan["pages"]}
        for p in scan["pages"]:
            graph.node(p["url"],label=urlsplit(p["url"]).path or "/",style="filled",fillcolor="#CBDCD0" if p["url"]==page["url"] else "#EDF0EB",shape="box")
        for src,dst in sorted({(l["from"],l["to"]) for l in scan["links"] if l["to"] in urls}):
            graph.edge(src,dst)
        st.graphviz_chart(graph,use_container_width=True)
        st.dataframe([{k:l.get(k) for k in ("from","to","label","result")} for l in scan["links"] if page["url"] in (l["from"],l["to"])],use_container_width=True,hide_index=True)
        st.subheader("Journey reachability")
        if scan["journey"]:
            st.dataframe([{k:v for k,v in step.items() if k!="screenshots"} for step in scan["journey"]],hide_index=True,use_container_width=True)
        else:
            st.caption("Add a sequence of paths under Scan / Brand rules and page journey.")
    with tab3:
        resources=[r for r in scan["resources"] if r["page"]==page["url"]]
        st.metric("Resources found in HTML",len(resources))
        st.dataframe([{k:r.get(k) for k in ("kind","url","status","bytes","seconds","result")} for r in resources],hide_index=True,use_container_width=True)
        st.caption("HTML-discovered resources only. JavaScript, CSS imports, caching and user location can change real download totals.")
    with tab4:
        manual_key="manual-"+hashlib.sha256(page["url"].encode()).hexdigest()[:20]
        saved=store.setting(manual_key,{})
        checks=["Keyboard focus is visible and follows a sensible order","Menus and dialogs work with keyboard and Escape","No keyboard trap blocks navigation","Forms explain required fields and errors","Controlled form test shows the expected success state","Mobile content is readable and important controls are reachable","Popups are dismissible and do not hide essential content","Print view preserves important information","Reduced-motion behavior is appropriate","Policy links, business details and claims have been reviewed"]
        st.caption("Record your own inspection. Test form submissions only on an authorized test environment; this app does not submit them.")
        with st.form("manual-review"):
            answers={}
            for text in checks:
                choices=["Not reviewed","Pass","Needs work","Not applicable"]
                answers[text]=st.selectbox(text,choices,index=choices.index(saved.get(text,"Not reviewed")))
            answers["notes"]=st.text_area("Review notes",saved.get("notes",""))
            if st.form_submit_button("Save checklist"):
                store.set_setting(manual_key,answers)
                st.success("Checklist saved.")

elif nav=="Compare":
    title("Before & after","See what changed.","Compare findings and screenshots. A missing finding can reflect a changed scan scope, so keep the coverage notes in view.")
    if len(history)<2:
        st.info("Save two scans of a website to compare them.")
        st.stop()
    a,b=st.columns(2)
    first=a.selectbox("Earlier scan",history,index=1,format_func=lambda h:h["created"]+" / "+h["url"],key="before")
    second=b.selectbox("Later scan",history,index=0,format_func=lambda h:h["created"]+" / "+h["url"],key="after")
    before,after=store.scan(first["id"]),store.scan(second["id"])
    result=compare_scans(before,after)
    if before["url"]!=after["url"]:
        st.warning("These scans use different starting URLs. Compare the scope carefully.")
    if not result["comparable"]:
        st.info("Coverage or options differ, or the later scan was incomplete. 'Not seen' does not mean fixed.")
    cols=st.columns(3)
    for col,key,label in zip(cols,["new","persistent","not_seen"],["New findings","Still present","Not seen this time"]):
        col.metric(label,len(result[key]))
        with col.expander("View findings"):
            st.dataframe([{k:i[k] for k in ("title","page","severity")} for i in result[key]],hide_index=True)
    shared=sorted({p["url"] for p in before["pages"]}&{p["url"] for p in after["pages"]})
    if shared:
        target=st.selectbox("Visual comparison page",shared)
        old=next(p for p in before["pages"] if p["url"]==target).get("browser",{}).get("screenshots",{})
        new=next(p for p in after["pages"] if p["url"]==target).get("browser",{}).get("screenshots",{})
        modes=[k for k in old if k in new and image_path(old[k]) and image_path(new[k])]
        if modes:
            mode=st.selectbox("Screenshot type",modes)
            a,b=st.columns(2)
            a.image(old[mode],caption="Before",use_container_width=True)
            b.image(new[mode],caption="After",use_container_width=True)
            data,ratio=visual_diff(old[mode],new[mode])
            st.image(data,caption=f"Changed pixels highlighted / {ratio:.1%}. Dynamic content, timing and fonts can cause differences.",use_container_width=True)
            st.download_button("Download highlighted comparison",data,"visual-comparison.png","image/png")
        else:
            st.caption("Matching browser screenshots are needed for a visual comparison.")

elif nav=="Reports":
    title("Ready to hand over","Turn findings into next steps.","Download a review report, a spreadsheet of issues, or a project archive to keep with your client work.")
    require_scan()
    summary(scan)
    brand=st.text_input("Report heading",store.setting("report_brand","Website Quality Checker"))
    logo=image_path(store.setting("report_logo",""))
    if st.button("Prepare PDF report",type="primary"):
        st.session_state["pdf_export"]={"id":scan["id"],"brand":brand,"data":pdf_report(scan,reviews,brand,str(logo) if logo else None)}
    export=st.session_state.get("pdf_export",{})
    if export.get("id")==scan["id"] and export.get("brand")==brand:
        st.download_button("Download PDF",export["data"],"website-review.pdf","application/pdf")
    st.download_button("Download findings CSV",csv_report(scan,reviews),"website-findings.csv","text/csv")
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("scan.json",json.dumps(scan,indent=2))
        z.writestr("reviews.json",json.dumps({i["id"]:reviews.get(i["id"],{}) for i in scan["issues"]},indent=2))
        z.writestr("findings.csv",csv_report(scan,reviews))
        manual={}
        for p in scan["pages"]:
            manual_key="manual-"+hashlib.sha256(p["url"].encode()).hexdigest()[:20]
            manual[p["url"]]=store.setting(manual_key,{})
            for path in p.get("browser",{}).get("screenshots",{}).values():
                file=image_path(path)
                if file:
                    z.write(file,"screenshots/"+file.name)
        z.writestr("manual-checklists.json",json.dumps(manual,indent=2))
        for issue in scan["issues"]:
            z.writestr("issue-cards/"+issue["id"]+".md",issue_markdown(issue,reviews.get(issue["id"],{})))
            file=image_path(reviews.get(issue["id"],{}).get("attachment"))
            if file:
                z.write(file,"evidence/"+file.name)
    st.download_button("Download scan archive",archive.getvalue(),"website-review-archive.zip","application/zip")
    st.caption("The archive contains extracted page text and screenshots. Review its contents before sharing.")

elif nav=="Schedules":
    title("Keep an eye on it","Make review a habit.","Save a daily or weekly scan. A separate worker runs it while your machine or server stays on.")
    heartbeat=store.setting("worker_heartbeat")
    active=heartbeat and (datetime.now(timezone.utc)-datetime.fromisoformat(heartbeat)).total_seconds()<90
    if active:
        st.success("The scheduler worker recently checked in.")
    else:
        st.warning("Scheduler worker is not currently reporting. Saved schedules will wait until it is running.")
        st.code("python worker.py",language="bash")
    if scan:
        with st.form("schedule"):
            st.write("Use this scan's URL and options: "+scan["url"])
            every=st.selectbox("Repeat",[24,168],format_func=lambda h:"Daily" if h==24 else "Weekly")
            if st.form_submit_button("Save recurring scan"):
                if ".test" in urlsplit(scan["url"]).hostname:
                    st.warning("Sample reports cannot be scheduled. Run a real scan first.")
                else:
                    store.schedule(scan["url"],every,scan["options"])
                    st.rerun()
    for job in store.schedules():
        with st.container(border=True):
            st.write(job["url"])
            st.caption(f"Every {job['hours']} hours / next run {job['next_run']} UTC")
            enabled=st.checkbox("Enabled",bool(job["enabled"]),key="schedule-"+str(job["id"]))
            if enabled != bool(job["enabled"]):
                store.toggle_schedule(job["id"],enabled)
                st.rerun()
    st.subheader("Notifications")
    for n in store.notifications():
        st.write(n["message"])
        st.caption(n["created"])
    if st.button("Mark notifications read"):
        store.mark_read()
    st.caption("Notifications stay inside this workspace. Email and messaging integrations are not configured.")

elif nav=="Settings":
    title("Your workspace","Make it yours.","Set reusable brand rules and report branding. Saved data belongs to this installation.")
    with st.form("settings"):
        brand=st.text_input("Default report heading",store.setting("report_brand","Lintel"))
        logo=st.file_uploader("Report logo (PNG or JPG)",type=["png","jpg","jpeg"])
        defaults=store.setting("brand_rules",{})
        dictionary=st.text_area("Custom dictionary (one word per line)","\n".join(defaults.get("dictionary",[])))
        required=st.text_area("Required phrases (one per line)","\n".join(defaults.get("required_phrases",[])))
        forbidden=st.text_area("Outdated wording (one per line)","\n".join(defaults.get("forbidden_phrases",[])))
        if st.form_submit_button("Save settings",type="primary"):
            try:
                if logo:
                    img=Image.open(logo);img.thumbnail((1200,1200))
                    path=store.artifacts/"report-logo.png";img.convert("RGB").save(path)
                    store.set_setting("report_logo",str(path))
                store.set_setting("report_brand",brand)
                store.set_setting("brand_rules",{"dictionary":lines(dictionary),"required_phrases":lines(required),"forbidden_phrases":lines(forbidden)})
                st.success("Settings saved.")
            except Exception as exc:
                st.error("Could not save settings: "+str(exc))
    st.subheader("Setup notes")
    st.write("Start with a one-page scan. Browser checks require a separate Chromium installation. Scheduling requires the worker process and persistent disk storage.")
    st.code("python -m playwright install chromium",language="bash")
    st.write("The app binds to localhost by default. Before hosting it for others, add deployment authentication, request limits and durable storage. All trusted workspace users can see its scans and notes.")
