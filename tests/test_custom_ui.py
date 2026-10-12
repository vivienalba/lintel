"""Optional Chromium integration checks; all website and AI data is simulated."""
import os
from pathlib import Path
import threading
import time

import pytest
from playwright.sync_api import sync_playwright, expect
import ai_service
import server
from sitecheck.demo import create_demo
from sitecheck.storage import Store


@pytest.fixture
def browser_ui(tmp_path, monkeypatch):
    executable = os.environ.get('LINTEL_TEST_BROWSER')
    if not executable:
        pytest.skip('Set LINTEL_TEST_BROWSER to a Chromium executable for UI checks.')
    monkeypatch.delenv('QUALITY_APP_PASSWORD', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-test-placeholder')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    store = Store(tmp_path)
    scan = store.save_scan(create_demo())
    monkeypatch.setattr(server, 'STORE', store)
    calls = []

    def explain(payload, key, model):
        calls.append(payload['kind'])
        return {'text': 'Explain the existing findings. <script>untrusted()</script>',
                'model': model, 'complete': True, 'input_tokens': 110, 'output_tokens': 30}

    def deep(payload, key, model, images):
        calls.append('page')
        # Keep the busy state observable to the browser checks.
        time.sleep(.35)
        finding = {'title': 'Clarify the next step', 'category': 'Content', 'priority': 'Medium',
                   'source_id': 'page_text', 'quote': payload['sources']['page_text'][:60],
                   'evidence_status': 'Quote matched to supplied source',
                   'reason': 'Visitors need a clear next step.',
                   'suggested_fix': 'Make the next action explicit.',
                   'verification': 'Ask a visitor to explain what to do next.'}
        return {'summary': 'Review the next step for visitors.', 'page': payload['page'],
                'findings': [finding], 'manual_checks': ['Check the contact form manually.'],
                'coverage': payload['coverage'], 'screenshot_count': len(images),
                'discarded_findings': 0, 'model': model, 'input_tokens': 220, 'output_tokens': 80}

    def answer(scan, question, key, model, history, audits):
        calls.append('chat')
        return {'text': 'Use the saved Python evidence to prioritize your fixes.',
                'model': model, 'complete': True, 'input_tokens': 200, 'output_tokens': 40}

    monkeypatch.setattr(ai_service, 'generate_review', explain)
    monkeypatch.setattr(ai_service, 'generate_page_audit', deep)
    monkeypatch.setattr(ai_service, 'answer_question', answer)
    http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:' + str(http.server_port)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=executable, headless=True,
                                     args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        yield page, url, scan, calls, errors
        browser.close()
    http.shutdown()
    http.server_close()


def no_overflow(page):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')


def capture(page, filename):
    folder = os.environ.get('LINTEL_UI_CAPTURES')
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / filename), full_page=True)


def test_desktop_reference_ui_and_ai_workflow(browser_ui):
    page, url, scan, calls, errors = browser_ui
    page.goto(url)
    expect(page.get_by_role('heading', name='Know your website. Inside out.')).to_be_visible()
    expect(page.locator('.hero-graphic')).to_be_visible()
    no_overflow(page)
    capture(page, 'desktop-home.png')
    assert calls == []
    page.goto(url + '/#report/' + scan['id'] + '/ai')
    expect(page.get_by_role('heading', name='Look deeper. Decide clearly.')).to_be_visible()
    expect(page.get_by_role('button', name='RUN DEEP AI REVIEW')).to_be_disabled()
    assert page.locator('.report-tabs [data-lintel-tab]').count() == 1
    assert calls == []
    page.locator('#ai-consent').check()
    expect(page.locator('#ai-consent')).to_be_focused()
    page.get_by_role('button', name='RUN DEEP AI REVIEW').click()
    expect(page.get_by_text('2 of 2 page reviews completed or reused.')).to_be_visible()
    expect(page.get_by_role('heading', name='Clarify the next step')).to_be_visible()
    expect(page.locator('.ai-finding-copy')).to_contain_text('Ask a visitor to explain')
    assert calls.count('page') == 2
    page.get_by_role('button', name='RUN DEEP AI REVIEW').click()
    expect(page.get_by_role('button', name='RUN DEEP AI REVIEW')).to_be_enabled()
    assert calls.count('page') == 2
    page.get_by_role('button', name='SUMMARIZE PYTHON FINDINGS').click()
    expect(page.get_by_role('heading', name='Python findings, explained.')).to_be_visible()
    assert page.locator('.ai-summary script').count() == 0
    expect(page.locator('.ai-summary')).to_contain_text('<script>untrusted()</script>')
    page.get_by_role('button', name='ASK LINTEL').click()
    expect(page.get_by_role('dialog', name='Ask Lintel')).to_be_visible()
    expect(page.get_by_role('button', name='SEND', exact=True)).to_be_disabled()
    page.locator('#ai-chat-consent').check()
    expect(page.locator('#ai-chat-consent')).to_be_focused()
    page.get_by_role('textbox', name='Ask about the selected website').fill('What should I fix first?')
    page.get_by_role('button', name='SEND', exact=True).click()
    expect(page.locator('.ai-chat-log')).to_contain_text('Use the saved Python evidence')
    assert calls.count('chat') == 1
    no_overflow(page)
    capture(page, 'desktop-ai-review-chat.png')
    page.get_by_role('textbox', name='Ask about the selected website').press('Escape')
    expect(page.get_by_role('dialog', name='Ask Lintel')).to_be_hidden()
    assert errors == []


def test_mobile_navigation_finding_help_and_side_chat(browser_ui):
    page, url, scan, calls, errors = browser_ui
    page.set_viewport_size({'width': 390, 'height': 844})
    page.emulate_media(reduced_motion='reduce')
    page.goto(url)
    expect(page.get_by_role('heading', name='Know your website. Inside out.')).to_be_visible()
    no_overflow(page)
    page.get_by_role('button', name='Toggle navigation').click()
    page.get_by_role('link', name='HISTORY', exact=True).click()
    expect(page.locator('.history-row')).to_be_visible()
    assert page.get_by_role('button', name='Toggle navigation').get_attribute('aria-expanded') == 'false'
    page.goto(url + '/#report/' + scan['id'] + '/findings')
    expect(page.locator('.finding-row').first).to_be_visible()
    page.locator('.finding-row').first.click()
    expect(page.get_by_role('button', name='EXPLAIN WITH AI')).to_be_disabled()
    page.locator('[data-ai-issue-consent]').check()
    page.get_by_role('button', name='EXPLAIN WITH AI').click()
    expect(page.locator('.ai-issue-output')).to_contain_text('Explain the existing findings.')
    page.get_by_role('button', name='ASK LINTEL').click()
    expect(page.get_by_role('dialog', name='Ask Lintel')).to_be_visible()
    no_overflow(page)
    bounds = page.locator('.ai-chat-panel').bounding_box()
    assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
    assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 844
    page.locator('#ai-chat-consent').check()
    page.get_by_role('textbox', name='Ask about the selected website').fill('How can I verify this fix?')
    page.get_by_role('button', name='SEND', exact=True).click()
    expect(page.locator('.ai-chat-log')).to_contain_text('Use the saved Python evidence')
    capture(page, 'mobile-chat.png')
    page.get_by_role('button', name='Close Ask Lintel').click()
    page.get_by_role('button', name='AI REVIEW', exact=True).click()
    expect(page.get_by_role('heading', name='Look deeper. Decide clearly.')).to_be_visible()
    no_overflow(page)
    capture(page, 'mobile-ai-review.png')
    assert calls.count('chat') == 1 and errors == []


def test_stop_review_finishes_current_page_and_reuses_it(browser_ui):
    page, url, scan, calls, errors = browser_ui
    page.goto(url + '/#report/' + scan['id'] + '/ai')
    page.locator('#ai-consent').check()
    page.get_by_role('button', name='RUN DEEP AI REVIEW').click()
    page.get_by_role('button', name='STOP AFTER THIS PAGE', exact=True).click()
    expect(page.get_by_text('1 of 2 page reviews completed or reused. Stopped after the current page.')).to_be_visible()
    assert calls.count('page') == 1
    page.get_by_role('button', name='RUN DEEP AI REVIEW').click()
    expect(page.get_by_text('2 of 2 page reviews completed or reused.')).to_be_visible()
    assert calls.count('page') == 2 and errors == []
