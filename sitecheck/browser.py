"""Optional browser inspection. Every HTTP request uses the pinned safe client.

No POST/PUT/DELETE, service workers, WebSockets, form submissions or permissions.
Screenshots are evidence from a constrained browser, not a full security audit.
"""
from pathlib import Path
import hashlib
from .network import normalize


DOM_CHECKS = r'''() => {
 const visible = e => {const r=e.getBoundingClientRect();const s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';};
 const desc=e=>({tag:e.tagName.toLowerCase(),text:(e.innerText||e.getAttribute('aria-label')||e.getAttribute('alt')||'').trim().slice(0,90),selector:e.id?'#'+e.id:e.tagName.toLowerCase()});
 const all=[...document.querySelectorAll('body *')].slice(0,5000).filter(visible);
 const overflow=all.filter(e=>{const r=e.getBoundingClientRect();return r.right>innerWidth+2&&r.left<innerWidth&&getComputedStyle(e).position!=='fixed';}).slice(0,15).map(desc);
 const small=[...document.querySelectorAll('a,button,input,select')].filter(visible).filter(e=>{const r=e.getBoundingClientRect();return r.width<24||r.height<24;}).slice(0,25).map(desc);
 const overlays=all.filter(e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect();return ['fixed','sticky'].includes(s.position)&&r.width*r.height>innerWidth*innerHeight*.3;}).slice(0,5).map(desc);
 const images=[...document.images].filter(visible).map(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {...desc(e),src:e.currentSrc,loaded:e.complete&&e.naturalWidth>0,stretched:e.naturalWidth>0&&Math.abs((r.width/r.height)/(e.naturalWidth/e.naturalHeight)-1)>.15&&s.objectFit==='fill'};});
 function rgb(v){const a=v.match(/[\d.]+/g);return a&&a.length>=3?a.map(Number):null;}
 function luminance(c){return c.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);}
 const contrast=[]; let contrastSkipped=0;
 for(const e of all){
   if(![...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()))continue;
   const s=getComputedStyle(e),fg=rgb(s.color);let bg=null,complex=false,node=e;
   while(node){const cs=getComputedStyle(node);if(cs.backgroundImage!=='none'||Number(cs.opacity)<1){complex=true;break;}const c=rgb(cs.backgroundColor);if(c&&(c[3]===undefined||c[3]===1)){bg=c;break;}if(c&&c[3]>0){complex=true;break;}node=node.parentElement;}
   if(complex||!fg||(fg[3]!==undefined&&fg[3]!==1)){contrastSkipped++;continue;}
   bg=bg||[255,255,255];const l1=luminance(fg),l2=luminance(bg),ratio=(Math.max(l1,l2)+.05)/(Math.min(l1,l2)+.05);
   const large=parseFloat(s.fontSize)>=24||(parseFloat(s.fontSize)>=18.66&&parseInt(s.fontWeight)>=700),threshold=large?3:4.5;
   if(ratio<threshold&&contrast.length<25)contrast.push({...desc(e),ratio:Math.round(ratio*100)/100,threshold});
 }
 const forms=[...document.forms].map((form,index)=>({index,required:[...form.elements].filter(e=>e.required).length,invalid:[...form.elements].filter(e=>e.willValidate&&!e.validity.valid).length,novalidate:form.noValidate}));
 const animations=document.getAnimations().map(a=>({duration:a.effect?.getTiming().duration,iterations:a.effect?.getTiming().iterations,playState:a.playState}));
 const perf=performance.getEntriesByType('navigation')[0];
 return {overflow,small,overlays,images,contrast,contrastSkipped,forms,animations,elementCount:all.length,navigation:perf?{domContentLoaded:perf.domContentLoadedEventEnd,load:perf.loadEventEnd}:null};
}'''


def inspect_browser(scan,options,client,artifact_dir,progress=None):
    from playwright.sync_api import sync_playwright
    folder = Path(artifact_dir or "data/artifacts")/scan.id
    folder.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,args=["--disable-background-networking","--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
        context = browser.new_context(service_workers="block",accept_downloads=False,permissions=[],viewport={"width":1440,"height":1000})
        blocked=[]
        def handle(route):
            request=route.request
            if request.method not in ("GET","HEAD"):
                blocked.append("Non-read request blocked: "+request.method)
                return route.abort()
            result=client.fetch(request.url,follow=False)
            if result.error or result.truncated or not result.status:
                blocked.append(request.url)
                return route.abort()
            headers={k:v for k,v in result.headers.items() if k not in ("content-length","transfer-encoding","connection","set-cookie","content-encoding")}
            try:
                route.fulfill(status=result.status,headers=headers,body=result.body)
            except Exception:
                route.abort()
        context.route("**/*",handle)
        context.route_web_socket("**/*",lambda ws:ws.close())
        context.add_init_script("window.open=()=>null; navigator.sendBeacon=()=>false;")
        urls=[record["url"] for record in scan.pages[:options.browser_pages]]
        urls += [step["url"] for step in scan.journey if step["result"]=="Working" and step["url"] not in urls]
        for browser_index, url in enumerate(urls[:10]):
            if progress:
                progress("Rendering desktop and mobile previews: "+url)
            record=next((r for r in scan.pages if r["url"]==url),None)
            result={"screenshots":{},"keyboard":[],"limitations":[]}
            slug=hashlib.sha256(url.encode()).hexdigest()[:12]
            page=context.new_page()
            page.on("dialog",lambda d:d.dismiss())
            before=len(blocked)
            try:
                page.goto(url,wait_until="domcontentloaded",timeout=25000)
                page.wait_for_timeout(300)
                for mode, width,height in [("desktop",1440,1000),("mobile",390,844)]:
                    page.set_viewport_size({"width":width,"height":height})
                    page.wait_for_timeout(150)
                    data=page.evaluate(DOM_CHECKS)
                    result[mode]=data
                    path=folder/f"{slug}-{mode}.png"
                    # Full-page images capped to keep user-provided pages bounded.
                    page.screenshot(path=str(path),full_page=False,animations="disabled",timeout=10000)
                    result["screenshots"][mode]=str(path.resolve())
                    if data["overflow"]:
                        scan.add(page=url,category="Accessibility",severity="Review",title=f"Possible horizontal overflow ({mode})",evidence=str(data["overflow"][:5]),fix="Inspect the screenshot and adjust widths, wrapping or positioning.",target=mode,code="overflow")
                    if mode=="mobile" and data["small"]:
                        scan.add(page=url,category="Accessibility",severity="Review",title="Small touch targets",evidence=str(data["small"][:8]),fix="Review target size and spacing. Inline links and sufficiently spaced targets can be exceptions.",code="touch-targets")
                    for item in data["contrast"]:
                        scan.add(page=url,category="Accessibility",severity="Review",title="Potential text contrast issue",evidence=f"{item['text']} — {item['ratio']}:1; reference threshold {item['threshold']}:1.",fix="Review the rendered foreground/background colors. This covers simple solid backgrounds, not every accessibility requirement.",target=item["selector"]+item["text"],code="contrast")
                    if data["overlays"]:
                        scan.add(page=url,category="Accessibility",severity="Review",title="Large overlay may obstruct content",evidence=str(data["overlays"]),fix="Check that the overlay is intentional, dismissible and keyboard accessible.",code="overlay")
                    for img in data["images"]:
                        if img["stretched"]:
                            scan.add(page=url,category="Images",severity="Review",title="Image may be stretched",evidence=img["src"],fix="Preserve the source aspect ratio or choose an appropriate object-fit value.",target=img["src"],code="stretched-image")
                page.set_viewport_size({"width":1440,"height":1000})
                page.evaluate("document.activeElement?.blur()")
                for _ in range(12):
                    page.keyboard.press("Tab")
                    item=page.evaluate("""() => {const e=document.activeElement,s=getComputedStyle(e),r=e.getBoundingClientRect();return {tag:e.tagName,text:(e.innerText||e.getAttribute('aria-label')||'').slice(0,90),id:e.id,outline:s.outlineStyle,outlineWidth:s.outlineWidth,boxShadow:s.boxShadow,inView:r.top>=0&&r.left>=0&&r.bottom<=innerHeight&&r.right<=innerWidth};}""")
                    result["keyboard"].append(item)
                path=folder/f"{slug}-focus.png"
                page.screenshot(path=str(path),animations="disabled")
                result["screenshots"]["keyboard focus"]=str(path.resolve())
                page.emulate_media(media="print")
                path=folder/f"{slug}-print.png"
                page.screenshot(path=str(path),animations="disabled")
                result["screenshots"]["print"]=str(path.resolve())
                page.emulate_media(media="screen",reduced_motion="reduce")
                page.reload(wait_until="domcontentloaded",timeout=20000)
                page.wait_for_timeout(200)
                reduced=page.evaluate(DOM_CHECKS)
                result["reduced_motion"]={"active_animations":reduced["animations"]}
                path=folder/f"{slug}-reduced-motion.png"
                page.screenshot(path=str(path),animations="disabled")
                result["screenshots"]["reduced motion"]=str(path.resolve())
                if any(a["playState"]=="running" for a in reduced["animations"]):
                    scan.add(page=url,category="Accessibility",severity="Review",title="Animations remain with reduced motion",evidence=f"{len(reduced['animations'])} animation(s) observed.",fix="Review which motion is essential and whether nonessential motion should stop.",code="reduced-motion")
                if options.form_checks:
                    result["form_constraints"]=reduced["forms"]
                    result["limitations"].append("Inspected browser validity state only. Server validation, submitted errors and success messages require a controlled manual test.")
                result["limitations"].append("Keyboard capture covers the first 12 Tab stops. Review focus visibility, order, menus and traps manually. Screenshots capture the viewport, not the whole page.")
            except Exception as exc:
                result["limitations"].append(str(exc)[:250])
                scan.complete=False
            finally:
                page.close()
            if len(blocked)>before:
                result["limitations"].append(f"{len(blocked)-before} request(s) blocked or unavailable. The rendered preview may be incomplete.")
                scan.complete=False
            if record is not None:
                record["browser"]=result
            for step in scan.journey:
                if step["url"]==url:
                    step["screenshots"]=result["screenshots"]
            getattr(progress, "work", lambda *args: None)("browser", browser_index+1, len(urls[:10]))
        context.close()
        browser.close()
    scan.notes.append("Browser results use a constrained, read-only session. Network timings are affected by interception and must not be treated as Core Web Vitals.")
