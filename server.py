"""Branded product interface. Run with: python server.py"""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs, unquote
from pathlib import Path
from dataclasses import asdict, fields
from datetime import datetime, timezone
import argparse, base64, hashlib, hmac, io, json, mimetypes, os, secrets, threading, time, zipfile
from PIL import Image
from sitecheck.models import Options, now
from sitecheck.network import normalize, ScanError
from sitecheck.scanner import run_scan
from sitecheck.storage import Store, compare_scans
from sitecheck.reports import csv_report, pdf_report, issue_markdown, visual_diff
from ai_review import AIReviewError
import ai_service

ROOT=Path(__file__).resolve().parent
WEB=ROOT/'web'
LOCK=threading.Lock()
JOBS={}
SESSIONS=set()
STORE=None


class CancelScan(BaseException): pass


def clean_options(raw):
    allowed={f.name for f in fields(Options)}
    if not isinstance(raw,dict) or set(raw)-allowed:
        raise ValueError('Invalid scan options.')
    data={**asdict(Options()),**raw}
    for key,low,high in [('max_pages',1,30),('max_requests',20,500),('max_seconds',30,600),('image_kb',100,10000),('browser_pages',1,5)]:
        data[key]=max(low,min(high,int(data[key])))
    for key in ['dictionary','required_phrases','forbidden_phrases','journey','categories']:
        if not isinstance(data[key],list) or not all(isinstance(v,str) for v in data[key]):
            raise ValueError('Invalid '+key)
        data[key]=[s[:500] for s in data[key]][:100]
    for key in ['browser','spelling','form_checks']:
        if not isinstance(data[key],bool): raise ValueError('Invalid '+key)
    if data['language'] not in ['en','es','fr','de','pt','it']: raise ValueError('Unsupported spelling language.')
    return Options(**data)


def summary(scan):
    counts={s:sum(i['severity']==s for i in scan['issues']) for s in ['Needs fixing','Review',"Couldn't verify"]}
    destinations={}
    for item in scan['links']+scan['resources']:
        url=item.get('to',item.get('url',''))
        if item.get('result')=='Working' and url:
            destinations[url]={'url':url,'status':item.get('status'),'page':item.get('from',item.get('page','')),'kind':item.get('kind'),'final_url':item.get('final_url',url)}
    return {'counts':counts,'verified':list(destinations.values()),'pages':len(scan['pages']),
            'status':'Needs attention' if counts['Needs fixing'] else 'Review findings' if counts['Review'] else 'Incomplete coverage' if counts["Couldn't verify"] or not scan.get('complete') else 'No issues detected',
            'score':None,'score_note':'This scanner reports evidence and coverage; it does not calculate a validated 0–100 quality score.'}


def phase_for(message):
    if message.startswith('Reading page'):return 'pages'
    if message.startswith('Checking page'):return 'pages'
    if message.startswith('Checking destination'):return 'destinations'
    if message.startswith('Comparing'):return 'content'
    if message.startswith('Rendering'):return 'browser'
    return 'preparing'


def start_job(url,options):
    with LOCK:
        if any(j['state'] in ['queued','running'] for j in JOBS.values()):
            raise ValueError('A scan is already running. Finish or cancel it before starting another.')
        # Bound in-memory history.
        for key in list(JOBS)[:-20]:JOBS.pop(key,None)
        job_id=secrets.token_hex(12)
        JOBS[job_id]={'id':job_id,'url':url,'state':'queued','phase':'connecting','percent':0,'work':None,'message':'Connecting to the website','events':[],'cancel':False,'created':now(),'options':asdict(options)}
    def work():
        job=JOBS[job_id]
        job['state']='running'
        def progress(message):
            if job['cancel']:raise CancelScan()
            with LOCK:
                job.update(message=message,phase=phase_for(message))
                job['events'].append({'message':message,'phase':job['phase'],'time':now()})
                job['events']=job['events'][-60:]
        weights=[('pages',35),('destinations',40),('content',13),*([('browser',10)] if options.browser else []),('preparing',2)]
        total_weight=sum(w for _,w in weights)
        def work_progress(phase,completed,total):
            if job['cancel']:raise CancelScan()
            offset=0
            for key,weight in weights:
                if key==phase:
                    percent=min(99,100*(offset+weight*min(1,completed/max(1,total)))/total_weight)
                    with LOCK:
                        job.update(percent=max(job['percent'],round(percent,2)),work={'phase':phase,'completed':completed,'total':total})
                    return
                offset+=weight
        progress.work=work_progress
        try:
            scan=run_scan(url,options,progress=progress,artifact_dir=STORE.artifacts)
            progress('Preparing report and saving findings')
            data=STORE.save_scan(scan)
            job.update(state='completed',phase='complete',percent=100,scan_id=data['id'],message='Analysis saved')
        except CancelScan:
            job.update(state='cancelled',message='Scan cancelled. No partial report was saved.')
        except Exception as exc:
            job.update(state='failed',message='The scan could not finish.',detail=str(exc)[:1500])
    threading.Thread(target=work,daemon=True).start()
    return job_id


def safe_artifact(path):
    p=Path(path).resolve()
    if not p.is_relative_to(STORE.artifacts) or not p.is_file():raise ValueError('Artifact not available.')
    return p


def options_json():return asdict(Options())


def archive(scan,reviews):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('scan.json',json.dumps(scan,indent=2))
        z.writestr('reviews.json',json.dumps({i['id']:reviews.get(i['id'],{}) for i in scan['issues']},indent=2))
        z.writestr('findings.csv',csv_report(scan,reviews))
        z.writestr('ai-reviews.json',json.dumps(ai_service.overview(STORE,scan['id']),indent=2))
        manual={}
        for page in scan['pages']:
            key='manual-'+hashlib.sha256(page['url'].encode()).hexdigest()[:20]
            manual[page['url']]=STORE.setting(key,{})
            for path in page.get('browser',{}).get('screenshots',{}).values():
                try:
                    p=safe_artifact(path);z.write(p,'screenshots/'+p.name)
                except ValueError:pass
        z.writestr('manual-checklists.json',json.dumps(manual,indent=2))
        for issue in scan['issues']:
            z.writestr('issue-cards/'+issue['id']+'.md',issue_markdown(issue,reviews.get(issue['id'],{})))
            path=reviews.get(issue['id'],{}).get('attachment')
            if path:
                try:
                    p=safe_artifact(path);z.write(p,'evidence/'+p.name)
                except ValueError:pass
    return out.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version='WebsiteChecker'
    def log_message(self,fmt,*args):pass
    def send(self,status,body,ctype='application/json',filename=None,cookie=None):
        if isinstance(body,(dict,list)):body=json.dumps(body).encode()
        if isinstance(body,str):body=body.encode()
        self.send_response(status)
        self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Cache-Control','no-store' if self.path.startswith('/api') else 'no-cache')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if filename:self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers();self.wfile.write(body)
    def authorized(self):
        if not os.environ.get('QUALITY_APP_PASSWORD'):return True
        return any(part.strip().startswith('wqc_session=') and part.strip().split('=',1)[1] in SESSIONS for part in self.headers.get('Cookie','').split(';'))
    def body(self):
        size=int(self.headers.get('Content-Length',0))
        if size>12_000_000:raise ValueError('Upload is too large. Use an image under 8 MB.')
        return json.loads(self.rfile.read(size) or b'{}')
    def do_GET(self):
        parsed=urlsplit(self.path);path=parsed.path;q=parse_qs(parsed.query)
        def val(key,default=''):return q.get(key,[default])[0]
        try:
            if path=='/api/health':return self.send(200,{'ok':True})
            if path=='/api/auth':return self.send(200,{'required':bool(os.environ.get('QUALITY_APP_PASSWORD')),'authorized':self.authorized()})
            if path.startswith('/api/') and not self.authorized():return self.send(401,{'error':'Enter the workspace password to continue.'})
            if path=='/api/ai/status':return self.send(200,ai_service.status())
            if path=='/api/ai/reviews':return self.send(200,ai_service.overview(STORE,val('id')))
            if path=='/api/boot':
                scans=STORE.scans()
                for row in scans:
                    data=STORE.scan(row['id']);row.update(summary=summary(data),sample=any('SAMPLE DATA' in n for n in data.get('notes',[])))
                return self.send(200,{'scans':scans,'reviews':STORE.reviews(),'options':options_json(),'settings':{'report_brand':STORE.setting('report_brand','Website Quality Checker'),'brand_rules':STORE.setting('brand_rules',{}),'report_logo':STORE.setting('report_logo','')},'schedules':STORE.schedules(),'notifications':STORE.notifications(),'heartbeat':STORE.setting('worker_heartbeat')})
            if path.startswith('/api/job/'):
                job=JOBS.get(path.rsplit('/',1)[-1])
                return self.send(200,{k:v for k,v in job.items() if k!='cancel'}) if job else self.send(404,{'error':'This scan session is no longer available. Check History for saved results.'})
            if path.startswith('/api/scan/'):
                scan=STORE.scan(path.rsplit('/',1)[-1])
                return self.send(200,{'scan':scan,'summary':summary(scan)}) if scan else self.send(404,{'error':'The saved scan was not found.'})
            if path=='/api/manual':
                key='manual-'+hashlib.sha256(val('page').encode()).hexdigest()[:20]
                return self.send(200,STORE.setting(key,{}))
            if path=='/api/artifact':
                p=safe_artifact(val('path'))
                return self.send(200,p.read_bytes(),mimetypes.guess_type(p.name)[0] or 'application/octet-stream')
            if path=='/api/compare':
                a,b=STORE.scan(val('before')),STORE.scan(val('after'))
                if not a or not b:raise ValueError('Select two saved scans.')
                return self.send(200,compare_scans(a,b))
            if path=='/api/diff':
                a,b=safe_artifact(val('before')),safe_artifact(val('after'))
                data,ratio=visual_diff(a,b)
                return self.send(200,{'image':base64.b64encode(data).decode(),'ratio':ratio})
            if path=='/api/export':
                scan=STORE.scan(val('id'))
                if not scan:raise ValueError('Select a saved scan to export.')
                reviews=STORE.reviews();kind=val('kind')
                if kind=='csv':return self.send(200,csv_report(scan,reviews),'text/csv','website-findings.csv')
                if kind=='pdf':
                    logo=STORE.setting('report_logo','')
                    logo=str(safe_artifact(logo)) if logo else None
                    return self.send(200,pdf_report(scan,reviews,val('brand',STORE.setting('report_brand','Website Quality Checker')),logo,ai_service.overview(STORE,scan['id'])),'application/pdf','website-review.pdf')
                if kind=='zip':return self.send(200,archive(scan,reviews),'application/zip','website-review-archive.zip')
                if kind=='card':
                    issue=next(i for i in scan['issues'] if i['id']==val('issue'))
                    return self.send(200,issue_markdown(issue,reviews.get(issue['id'],{})),'text/markdown','issue-'+issue['id']+'.md')
                raise ValueError('Unknown report format.')
            if path.startswith('/api/'):return self.send(404,{'error':'This action was not found.'})
            file=(WEB/unquote(path).lstrip('/')).resolve() if path!='/' else WEB/'index.html'
            if not file.is_relative_to(WEB) or not file.is_file():return self.send(404,'Not found','text/plain')
            return self.send(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        except (ValueError,KeyError,StopIteration,AIReviewError) as exc:return self.send(400,{'error':str(exc)})
        except Exception as exc:return self.send(500,{'error':'This action could not finish. Try again.','detail':str(exc)[:1000]})
    def do_POST(self):
        path=urlsplit(self.path).path
        origin=self.headers.get('Origin','')
        if origin and urlsplit(origin).netloc!=self.headers.get('Host'):return self.send(403,{'error':'Request origin did not match.'})
        if self.headers.get('X-Requested-With')!='WebsiteChecker':return self.send(403,{'error':'Request verification failed.'})
        if path!='/api/login' and not self.authorized():return self.send(401,{'error':'Enter the workspace password to continue.'})
        try:
            data=self.body()
            if path in ['/api/ai/summary','/api/ai/page','/api/ai/issue','/api/ai/chat']:
                return self.send(200,ai_service.perform(STORE,path.rsplit('/',1)[-1],data))
            if path=='/api/login':
                if not hmac.compare_digest(str(data.get('password','')),os.environ.get('QUALITY_APP_PASSWORD','')):return self.send(401,{'error':'The password did not match.'})
                token=secrets.token_urlsafe(32);SESSIONS.add(token)
                return self.send(200,{'ok':True},cookie='wqc_session='+token+'; HttpOnly; SameSite=Strict; Path=/')
            if path=='/api/scan':
                if data.get('consent') is not True:raise ValueError('Confirm permission before scanning.')
                url=normalize(str(data.get('url','')))
                return self.send(202,{'job_id':start_job(url,clean_options(data.get('options',{})))})
            if path=='/api/cancel':
                job=JOBS.get(data['id'])
                if job:job['cancel']=True
                return self.send(200,{'ok':True})
            if path=='/api/review':
                rid=data['id'];payload=data['review']
                if payload.get('status') not in ['Open','In progress','Fixed (manual)','Intentional','Needs review']:raise ValueError('Unknown review status.')
                if payload.get('status')=='Intentional' and not payload.get('notes','').strip():raise ValueError('Add a reason for the intentional exception.')
                previous=STORE.reviews().get(rid,{})
                payload['attachment']=previous.get('attachment','')
                if data.get('image'):
                    raw=base64.b64decode(data['image'],validate=True)
                    if len(raw)>8_000_000:raise ValueError('Image must be under 8 MB.')
                    image=Image.open(io.BytesIO(raw));image.thumbnail((2400,2400))
                    target=STORE.artifacts/(hashlib.sha256(rid.encode()).hexdigest()+'-attachment.png')
                    image.convert('RGB').save(target);payload['attachment']=str(target)
                STORE.review(rid,payload)
                return self.send(200,{'ok':True,'review':payload})
            if path=='/api/manual':
                key='manual-'+hashlib.sha256(data['page'].encode()).hexdigest()[:20]
                STORE.set_setting(key,data['review']);return self.send(200,{'ok':True})
            if path=='/api/settings':
                STORE.set_setting('report_brand',str(data.get('report_brand','Website Quality Checker'))[:200])
                STORE.set_setting('brand_rules',data.get('brand_rules',{}))
                if data.get('image'):
                    raw=base64.b64decode(data['image'],validate=True)
                    image=Image.open(io.BytesIO(raw));image.thumbnail((1200,1200))
                    target=STORE.artifacts/'report-logo.png';image.convert('RGB').save(target)
                    STORE.set_setting('report_logo',str(target))
                return self.send(200,{'ok':True})
            if path=='/api/schedule':
                scan=STORE.scan(data['scan_id'])
                if not scan or any('SAMPLE DATA' in n for n in scan['notes']):raise ValueError('Choose a real saved scan to schedule.')
                hours=int(data.get('hours',168))
                if hours not in [24,168]:raise ValueError('Choose daily or weekly.')
                STORE.schedule(scan['url'],hours,scan['options']);return self.send(200,{'ok':True})
            if path=='/api/schedule/toggle':STORE.toggle_schedule(int(data['id']),bool(data['enabled']));return self.send(200,{'ok':True})
            if path=='/api/notifications/read':STORE.mark_read();return self.send(200,{'ok':True})
            return self.send(404,{'error':'This action was not found.'})
        except (ValueError,ScanError,KeyError,AIReviewError) as exc:return self.send(400,{'error':str(exc)})
        except Exception as exc:return self.send(500,{'error':'This action could not finish. Try again.','detail':str(exc)[:1000]})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8501);parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--open-browser',action='store_true')
    args=parser.parse_args();STORE=Store(ROOT/'data' if not os.environ.get('QUALITY_DATA_DIR') else None)
    print(f'Website Quality Checker: http://{args.host}:{args.port}',flush=True)
    http=ThreadingHTTPServer((args.host,args.port),Handler)
    if args.open_browser:
        import webbrowser
        threading.Timer(.3,lambda:webbrowser.open(f'http://localhost:{args.port}')).start()
    http.serve_forever()
