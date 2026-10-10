from dataclasses import asdict
from unittest.mock import patch
import socket
import pytest
from sitecheck.network import normalize,public_addresses,ScanError,SafeClient,Response
from sitecheck.models import Options,Scan
from sitecheck.scanner import run_scan,parse_page,status_label
from sitecheck.demo import DemoClient,create_demo
from sitecheck.storage import Store,compare_scans
from sitecheck.reports import csv_report


@pytest.mark.parametrize('url',['http://user:password@example.com/','file:///etc/passwd','http://example.com:8080/'])
def test_reject_unsafe_url_shapes(url):
    with pytest.raises(ScanError):
        normalize(url)


@pytest.mark.parametrize('address',['127.0.0.1','10.1.2.3','169.254.169.254','::1','::ffff:127.0.0.1','192.168.1.1'])
def test_private_and_metadata_blocked(address):
    info=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))]
    with patch('socket.getaddrinfo',return_value=info),pytest.raises(ScanError):
        public_addresses('example.com',443)


def test_all_dns_answers_checked():
    answers=[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('127.0.0.1',443))]
    with patch('socket.getaddrinfo',return_value=answers),pytest.raises(ScanError):
        public_addresses('example.com',443)


def test_redirect_loop_is_unverified():
    net=SafeClient()
    with patch.object(net,'allowed',return_value=True),patch.object(net,'_one',return_value=Response('https://x.test/',301,{'location':'https://x.test/'})):
        result=net.fetch('https://x.test/')
    assert 'loop' in result.error.lower()
    assert status_label(result)=="Couldn't verify"


def test_redirect_destination_revalidated():
    net=SafeClient()
    with patch.object(net,'allowed',return_value=True),patch.object(net,'_one',side_effect=[Response('https://x.test/',302,{'location':'http://127.0.0.1/'}),ScanError('Private addresses blocked')]) as fetch:
        result=net.fetch('https://x.test/')
    assert 'Private' in result.error
    assert fetch.call_args.args[0]=='http://127.0.0.1/'


@pytest.mark.parametrize('status,expected',[(404,'Needs fixing'),(403,"Couldn't verify"),(429,"Couldn't verify"),(503,"Couldn't verify"),(200,'Working')])
def test_taxonomy(status,expected):
    assert status_label(Response('https://x.test/',status))==expected


def test_demo_checks_real_html():
    scan=create_demo()
    titles={i['title'] for i in scan.issues}
    assert len(scan.pages)==2
    assert {'Broken destination','Image missing alt attribute','Form field may lack a label','Repeated page title','Possible unfinished content'}<=titles
    assert any(i['code']=='anchor' for i in scan.issues)


def test_decorative_alt_and_wrapped_label_are_valid():
    scan=Scan('https://x.test/',{})
    parse_page(scan,Response('https://x.test/',200,{},b'<html lang="en"><title>Test</title><img alt="" width="20" height="20"><label>Email<input type="email"></label></html>'),0,Options())
    assert not any(i['title'] in ('Image missing alt attribute','Form field may lack a label') for i in scan.issues)


def test_brand_rules_and_stable_ids():
    a,b=Scan('https://x.test/',{}),Scan('https://x.test/',{})
    options=Options(required_phrases=['New brand'],forbidden_phrases=['Old brand'])
    response=Response('https://x.test/',200,{},b'<html><body>Old brand</body></html>')
    for scan in (a,b):
        parse_page(scan,response,0,options)
    assert {i['id'] for i in a.issues}=={i['id'] for i in b.issues}
    assert len([i for i in a.issues if i['category']=='Brand'])==2


def test_store_review_persists_and_partial_not_claimed_fixed(tmp_path):
    store=Store(tmp_path)
    first=store.save_scan(create_demo())
    issue=first['issues'][0]
    store.review(issue['id'],{'status':'Intentional','notes':'Reviewed'})
    assert Store(tmp_path).reviews()[issue['id']]['notes']=='Reviewed'
    second={**first,'issues':[],'complete':False}
    diff=compare_scans(first,second)
    assert not diff['comparable'] and len(diff['not_seen'])==len(first['issues'])


def test_csv_formula_injection_escaped():
    scan=create_demo().to_dict()
    scan['issues'][0]['evidence']='=HYPERLINK("bad")'
    assert "'=HYPERLINK" in csv_report(scan,{}).decode('utf-8-sig')


def test_schedule_claim_once(tmp_path):
    store=Store(tmp_path)
    store.schedule('https://example.com',24,asdict(Options()))
    with store.connect() as db:
        db.execute("UPDATE schedules SET next_run='2000-01-01T00:00:00+00:00'")
    assert store.claim_due() is not None
    assert store.claim_due() is None


def test_missing_browser_fails_honestly(tmp_path):
    with patch('sitecheck.browser.inspect_browser',side_effect=RuntimeError('Browser unavailable')):
        scan=run_scan('https://sample-studio.test/',Options(browser=True),client=DemoClient(),artifact_dir=tmp_path)
    assert not scan.complete
    assert any('Browser unavailable' in note for note in scan.notes)


@pytest.mark.parametrize('response,expected',[
    (Response('https://x.test/robots.txt',error='CERTIFICATE_VERIFY_FAILED'), 'CERTIFICATE_VERIFY_FAILED'),
    (Response('https://x.test/robots.txt',403), 'HTTP 403'),
    (Response('https://x.test/robots.txt',429), 'HTTP 429'),
    (Response('https://x.test/robots.txt',200,{},b'User-agent: *\nDisallow: /'), 'disallows'),
])
def test_robots_failure_preserves_actual_reason(response,expected):
    net=SafeClient()
    with patch.object(net,'_one',return_value=response) as fetch:
        result=net.fetch('https://x.test/')
    assert expected in result.error
    assert status_label(result)=="Couldn't verify"
    assert fetch.call_count==1


def test_missing_robots_allows_public_page():
    net=SafeClient()
    with patch.object(net,'_one',side_effect=[Response('https://x.test/robots.txt',404),Response('https://x.test/',200,{},b'<html></html>')]):
        assert net.fetch('https://x.test/').status==200


def test_tls_keeps_verification_and_system_trust():
    import ssl
    from sitecheck.network import tls_context
    context=tls_context()
    assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
    assert context.cert_store_stats()['x509_ca']>0
