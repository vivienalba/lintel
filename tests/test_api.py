import json
import threading
import time
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
import pytest
import server
from sitecheck.storage import Store
from sitecheck.demo import create_demo,DemoClient
from sitecheck.scanner import run_scan


@pytest.fixture
def api(tmp_path,monkeypatch):
    monkeypatch.delenv('QUALITY_APP_PASSWORD',raising=False)
    monkeypatch.setattr(server,'STORE',Store(tmp_path))
    server.JOBS.clear();server.SESSIONS.clear()
    http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    root='http://127.0.0.1:'+str(http.server_port)
    opener=build_opener(ProxyHandler({}))
    def call(path,data=None,headers=None):
        request=Request(root+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-Requested-With':'WebsiteChecker',**(headers or {})})
        try:
            with opener.open(request,timeout=5) as r:
                body=r.read();kind=r.headers.get('Content-Type','')
                return r.status,json.loads(body) if 'application/json' in kind else body,r.headers
        except HTTPError as exc:
            return exc.code,json.loads(exc.read()),exc.headers
    yield call
    http.shutdown();http.server_close()


def test_boot_empty_and_static_fonts(api):
    status,data,_=api('/api/boot')
    assert status==200 and data['scans']==[]
    for path in ['/','/app.js','/assets/OpenSauceSans-Bold.ttf','/assets/SpaceMono-Regular.ttf']:
        assert api(path)[0]==200


def test_auth_and_origin(api,monkeypatch):
    monkeypatch.setenv('QUALITY_APP_PASSWORD','testing-password')
    assert api('/api/boot')[0]==401
    status,_,headers=api('/api/login',{'password':'testing-password'})
    assert status==200
    cookie=headers['Set-Cookie'].split(';')[0]
    assert api('/api/boot',headers={'Cookie':cookie})[0]==200
    assert api('/api/settings',{},headers={'Origin':'https://other.test','Cookie':cookie})[0]==403


def test_scan_requires_permission_and_valid_url(api):
    assert api('/api/scan',{'url':'https://example.com','consent':False})[0]==400
    assert api('/api/scan',{'url':'file:///etc/passwd','consent':True})[0]==400
    assert api('/api/scan',{'url':'not a url','consent':True})[0]==400


def test_scan_job_and_real_summary(api,monkeypatch):
    monkeypatch.setattr(server,'run_scan',lambda url,options,progress,artifact_dir:run_scan(url,options,progress,client=DemoClient()))
    status,data,_=api('/api/scan',{'url':'https://sample-studio.test/','consent':True,'options':{'max_pages':3}})
    assert status==202
    for _ in range(100):
        _,job,_=api('/api/job/'+data['job_id'])
        if job['state'] in ('completed','failed'):break
        time.sleep(.01)
    assert job['state']=='completed'
    assert job['percent']==100
    assert job['work']['phase']=='preparing'
    _,data,_=api('/api/scan/'+job['scan_id'])
    assert data['summary']['score'] is None
    assert data['summary']['counts']['Needs fixing']>0
    assert len(data['scan']['pages'])==2


def test_review_exports_and_checklist(api):
    scan=server.STORE.save_scan(create_demo())
    rid=scan['issues'][0]['id']
    assert api('/api/review',{'id':rid,'review':{'status':'Intentional','notes':''}})[0]==400
    assert api('/api/review',{'id':rid,'review':{'status':'In progress','owner':'Vivien','notes':'Review target','blocker':True}})[0]==200
    assert api('/api/manual',{'page':scan['pages'][0]['url'],'review':{'notes':'Keyboard reviewed'}})[0]==200
    for kind in ['csv','pdf','zip','card']:
        status,body,_=api('/api/export?id='+scan['id']+'&kind='+kind+'&issue='+rid)
        assert status==200 and len(body)>20


def test_unsafe_artifact_path_blocked(api):
    assert api('/api/artifact?path=/etc/passwd')[0]==400


def test_compare_and_real_only_scheduling(api):
    before=server.STORE.save_scan(create_demo())
    after=server.STORE.save_scan(create_demo())
    assert api('/api/compare?before='+before['id']+'&after='+after['id'])[0]==200
    assert api('/api/schedule',{'scan_id':before['id'],'hours':24})[0]==400
    after['notes']=[];server.STORE.save_scan(after)
    assert api('/api/schedule',{'scan_id':after['id'],'hours':24})[0]==200
    assert len(server.STORE.schedules())==1


def test_scanner_progress_reports_work_not_elapsed_time():
    snapshots=[]
    def progress(message):
        pass
    progress.work=lambda phase,completed,total:snapshots.append((phase,completed,total))
    run_scan('https://sample-studio.test/',progress=progress,client=DemoClient())
    assert all(0 <= completed <= total and total >= 1 for _,completed,total in snapshots)
    assert ('pages',1,1) in snapshots
    destinations=[x for x in snapshots if x[0]=='destinations']
    assert len(destinations)>2 and destinations[-1]==('destinations',1,1)
    assert ('content',1,1) in snapshots
    assert not any(phase=='browser' for phase,_,_ in snapshots)
    assert snapshots[-1]==('preparing',0,1)
