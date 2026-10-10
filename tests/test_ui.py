from streamlit.testing.v1 import AppTest
from sitecheck.storage import Store
from sitecheck.demo import create_demo
from pathlib import Path
APP=Path(__file__).resolve().parents[1]/'app.py'


def test_all_workspace_views(tmp_path,monkeypatch):
    monkeypatch.setenv('QUALITY_DATA_DIR',str(tmp_path))
    store=Store(tmp_path)
    store.save_scan(create_demo())
    store.save_scan(create_demo())
    app=AppTest.from_file(APP,default_timeout=30).run()
    assert not app.exception
    for section in ['Issues','Pages','Compare','Reports','Schedules','Settings','Scan']:
        app.radio(key='navigation').set_value(section).run()
        assert not app.exception,[e.message for e in app.exception]


def test_demo_and_settings(tmp_path,monkeypatch):
    monkeypatch.setenv('QUALITY_DATA_DIR',str(tmp_path))
    app=AppTest.from_file(APP,default_timeout=30).run()
    next(b for b in app.button if b.label=='Explore a sample report').click().run()
    assert not app.exception
    assert len(Store(tmp_path).scans())==1
    app.radio(key='navigation').set_value('Settings').run()
    next(t for t in app.text_input if t.label=='Default report heading').set_value('Vivien Studio Review')
    next(b for b in app.button if b.label=='Save settings').click().run()
    assert not app.exception
    assert Store(tmp_path).setting('report_brand')=='Vivien Studio Review'
