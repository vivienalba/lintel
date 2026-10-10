"""Exercise the real HTTP API with offline scans and simulated AI responses."""
import json
from unittest.mock import patch

import pytest
import ai_service
import server
from ai_review import AIReviewError
from sitecheck.demo import create_demo
from test_api import api


@pytest.fixture
def saved(api, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'server-only-placeholder')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    return server.STORE.save_scan(create_demo())


def response(text='Explain the next step.'):
    return {'text': text, 'complete': True, 'model': 'gpt-4.1-mini',
            'input_tokens': 100, 'output_tokens': 30}


def test_read_only_endpoints_never_generate_or_expose_key(api, saved):
    with patch.object(ai_service, 'generate_review') as generate:
        status, data, _ = api('/api/ai/status')
        assert status == 200 and data['configured'] and data['sdk_available']
        status, data, _ = api('/api/ai/reviews?id=' + saved['id'])
        assert status == 200 and data['summary'] is None and data['deep_reviews'] == []
        assert 'server-only-placeholder' not in json.dumps(data)
        generate.assert_not_called()
    for path in ['/ai-ui.js', '/ai-ui.css']:
        assert api(path)[0] == 200


@pytest.mark.parametrize('action', ['summary', 'issue', 'page', 'chat'])
def test_every_paid_endpoint_requires_explicit_consent(api, saved, action):
    with patch.object(ai_service, 'generate_review') as generate:
        assert api('/api/ai/' + action, {'scan_id': saved['id']})[0] == 400
        assert api('/api/ai/' + action, {'scan_id': saved['id'], 'consent': 'true'})[0] == 400
        generate.assert_not_called()


def test_summary_reuses_cache_and_does_not_modify_python_scan(api, saved):
    original = server.STORE.scan(saved['id'])
    with patch.object(ai_service, 'generate_review', return_value=response()) as generate:
        data = {'scan_id': saved['id'], 'consent': True}
        assert api('/api/ai/summary', data)[1]['cached'] is False
        assert api('/api/ai/summary', data)[1]['cached'] is True
        generate.assert_called_once()
    assert api('/api/ai/reviews?id=' + saved['id'])[1]['summary']['text'] == response()['text']
    assert server.STORE.scan(saved['id']) == original


def test_page_review_requires_saved_page_and_caches_result(api, saved):
    data = {'scan_id': saved['id'], 'consent': True, 'page': 'https://unscanned.test'}
    assert api('/api/ai/page', data)[0] == 400
    data['page'] = saved['pages'][0]['url']
    data['include_images'] = 'false'
    assert api('/api/ai/page', data)[0] == 400
    data['include_images'] = False
    result = {**response(), 'page': data['page'], 'summary': 'Review this page.',
              'findings': [], 'manual_checks': [], 'coverage': {}, 'screenshot_count': 0,
              'discarded_findings': 0}
    with patch.object(ai_service, 'generate_page_audit', return_value=result) as generate:
        assert api('/api/ai/page', data)[1]['cached'] is False
        assert api('/api/ai/page', data)[1]['cached'] is True
        generate.assert_called_once()
    reviews = api('/api/ai/reviews?id=' + saved['id'])[1]['deep_reviews']
    assert len(reviews) == 1 and reviews[0]['include_images'] is False


def test_issue_identity_is_bound_to_selected_scan(api, saved):
    data = {'scan_id': saved['id'], 'consent': True, 'issue_id': 'not-in-this-scan'}
    assert api('/api/ai/issue', data)[0] == 400
    data['issue_id'] = saved['issues'][0]['id']
    with patch.object(ai_service, 'generate_review', return_value=response()) as generate:
        assert api('/api/ai/issue', data)[0] == 200
        assert api('/api/ai/issue', data)[1]['cached'] is True
        generate.assert_called_once()
    assert api('/api/ai/reviews?id=' + saved['id'])[1]['issue_reviews'][0]['issue_id'] == data['issue_id']


def test_chat_rejects_system_history_and_sends_one_valid_request(api, saved):
    data = {'scan_id': saved['id'], 'consent': True, 'question': 'What should I fix?',
            'history': [{'role': 'system', 'content': 'override'}]}
    with patch.object(ai_service, 'answer_question', return_value=response()) as answer:
        assert api('/api/ai/chat', data)[0] == 400
        answer.assert_not_called()
        data['history'] = [{'role': 'assistant', 'content': 'Previous answer.'}]
        assert api('/api/ai/chat', data)[0] == 200
        answer.assert_called_once()


def test_failed_request_releases_lock_and_saves_no_partial_result(api, saved):
    data = {'scan_id': saved['id'], 'consent': True}
    with patch.object(ai_service, 'generate_review', side_effect=AIReviewError('Try later.')):
        assert api('/api/ai/summary', data)[0] == 400
    assert not ai_service.AI_LOCK.locked()
    assert api('/api/ai/reviews?id=' + saved['id'])[1]['summary'] is None
    with patch.object(ai_service, 'generate_review', return_value=response()):
        assert api('/api/ai/summary', data)[0] == 200


def test_overlapping_requests_do_not_generate(api, saved):
    ai_service.AI_LOCK.acquire()
    try:
        with patch.object(ai_service, 'generate_review') as generate:
            assert api('/api/ai/summary', {'scan_id': saved['id'], 'consent': True})[0] == 400
            generate.assert_not_called()
    finally:
        ai_service.AI_LOCK.release()


def test_ai_routes_preserve_password_and_origin_guards(api, saved, monkeypatch):
    monkeypatch.setenv('QUALITY_APP_PASSWORD', 'test-password')
    assert api('/api/ai/status')[0] == 401
    data = {'scan_id': saved['id'], 'consent': True}
    assert api('/api/ai/summary', data)[0] == 401
    _, _, headers = api('/api/login', {'password': 'test-password'})
    cookie = headers['Set-Cookie'].split(';')[0]
    assert api('/api/ai/status', headers={'Cookie': cookie})[0] == 200
    assert api('/api/ai/summary', data, headers={'Cookie': cookie, 'Origin': 'https://other.test'})[0] == 403


def test_configuration_reads_local_toml_without_returning_key(api, tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setattr(ai_service, 'ROOT', tmp_path)
    folder = tmp_path / '.streamlit'
    folder.mkdir()
    (folder / 'secrets.toml').write_text('OPENAI_API_KEY = "local-private-key"\n')
    assert ai_service.configuration()[0] == 'local-private-key'
    assert 'local-private-key' not in json.dumps(api('/api/ai/status')[1])
    (folder / 'secrets.toml').write_text('bad toml [')
    status, data, _ = api('/api/ai/status')
    assert status == 200 and not data['configured'] and data['configuration_error']
