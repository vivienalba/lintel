// The reference interface remains bundled in app.js. This module adds AI controls
// through the existing saved-scan routes and Python API, without replacing it.
const root = document.querySelector('#lintel-ai-root');
const main = document.querySelector('main');
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ui = {config:null,scan:null,reviews:{deep_reviews:[],issue_reviews:[]},open:false,busy:false,
  deepBusy:false,stop:false,selected:new Set(),images:false,consent:false,issueConsent:new Set(),
  chatConsent:new Set(),chats:new Map(),drafts:new Map(),error:'',deepError:'',status:'',token:0};
let configPending = false, contextPending = '', observed = false;

async function request(path, data) {
  const response = await fetch(path, {method:data === undefined ? 'GET' : 'POST',
    headers:data === undefined ? {} : {'Content-Type':'application/json','X-Requested-With':'WebsiteChecker'},
    body:data === undefined ? undefined : JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'This action could not finish.');
  return result;
}
const ready = () => Boolean(ui.config?.configured && ui.config?.sdk_available);
const paragraph = text => `<p class="ai-plain">${escape(text)}</p>`;
const enabled = condition => condition ? '' : 'disabled';
const pagePath = value => {try{return new URL(value).pathname || '/';}catch{return value;}};
// Keep conversations for this browser tab, scoped to each saved scan.
const CHAT_STORAGE_KEY = 'webchecker-lintel-chats-v2';
try {
  const saved = JSON.parse(sessionStorage.getItem(CHAT_STORAGE_KEY) || '{}');
  for (const [id, messages] of Object.entries(saved)) {
    if (Array.isArray(messages)) ui.chats.set(id, messages.filter(m => ['user','assistant'].includes(m.role) && typeof m.content === 'string').slice(-24));
  }
} catch (_) { /* Browser storage is optional. */ }
function saveChats() {
  try { sessionStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(Object.fromEntries(ui.chats))); }
  catch (_) { /* Private browsing or storage limits may prevent persistence. */ }
}
const chatHistory = () => ui.chats.get(ui.scan?.id) || [];

function setupNote() {
  if (ready()) return `<p class="small-note">${escape(ui.config.model)} / Optional AI requests use your API account.</p>`;
  return `<div class="note-box"><strong>${ui.config?.configured ? 'Install the AI dependency.' : 'Connect your AI key.'}</strong>
    <p>Add OPENAI_API_KEY to .streamlit/secrets.toml or your server environment, install requirements.txt, and restart python server.py.</p>
    ${ui.config?.configuration_error ? paragraph(ui.config.configuration_error) : ''}</div>`;
}

function plainResult(result) {
  if (!result) return '';
  return `<div class="ai-output">${result.complete === false ? '<p role="status">This explanation may be incomplete.</p>' : ''}
    ${paragraph(result.text)}<p class="small-note">${escape(result.model)} / ${Number(result.input_tokens || 0).toLocaleString()} input tokens / ${Number(result.output_tokens || 0).toLocaleString()} output tokens</p></div>`;
}

function pageResult(result) {
  if (!result) return '<p class="small-note">Select pages above and run a deep review to inspect their content.</p>';
  const coverage = result.coverage || {};
  return `<div class="ai-page-result">${paragraph(result.summary)}
    <p class="small-note">${Number(coverage.supplied_text_characters || 0).toLocaleString()} characters supplied / ${result.screenshot_count || 0} screenshot(s). A matched quote confirms the source, not the interpretation.</p>
    ${coverage.source_text_shortened || coverage.omitted_evidence_sources || coverage.html_truncated || coverage.stored_text_may_be_truncated ? '<div class="note-box">Some page evidence was shortened or omitted. Review the recorded coverage.</div>' : ''}
    ${result.discarded_findings ? `<p class="small-note">Python removed ${result.discarded_findings} unsupported, duplicate or malformed AI suggestion(s).</p>` : ''}
    ${result.findings.map(item => `<article class="ai-finding"><div class="ai-finding-head"><span class="label">AI SUGGESTION / ${escape(item.priority)} / ${escape(item.category)}</span><h3>${escape(item.title)}</h3></div>
      <div class="ai-evidence"><span class="label">SUPPORTING EVIDENCE</span>${paragraph(item.quote)}<p class="small-note">${escape(item.evidence_status)} / ${escape(item.source_id)}</p></div>
      <div class="ai-finding-copy"><span class="label">WHY REVIEW IT</span>${paragraph(item.reason)}<span class="label">PROPOSED FIX</span>${paragraph(item.suggested_fix)}<span class="label">HOW TO VERIFY</span>${paragraph(item.verification)}</div></article>`).join('')}
    ${!result.findings.length ? '<p class="small-note">No additional evidence-grounded AI suggestions were retained. This does not certify the page.</p>' : ''}
    ${result.manual_checks.length ? `<div class="ai-manual"><span class="label">MANUAL CHECKS</span>${result.manual_checks.map(paragraph).join('')}</div>` : ''}
    <p class="small-note">${escape(result.model)} / ${result.input_tokens || 0} input tokens / ${result.output_tokens || 0} output tokens</p></div>`;
}

function reviewView() {
  const pages = ui.scan.pages;
  const previews = pages.some(page => page.browser?.screenshots?.desktop || page.browser?.screenshots?.mobile);
  const results = ui.reviews.deep_reviews || [];
  return `<section class="ai-review" data-lintel-view="${escape(ui.scan.id)}"><div class="section-heading"><div><span class="label">PYTHON CHECKS + AI CONTEXT</span><h2 style="margin-top:12px">Look deeper. Decide clearly.</h2><p>Inspect the saved page content and keep AI suggestions beside the evidence.</p></div></div>
    ${setupNote()}<div class="ai-control-grid"><div><h3>Choose the pages.</h3><p class="small-note">Up to 30 scanned pages. Source content, headings, forms, links and browser observations are reviewed.</p>
      <div class="ai-page-options">${pages.map((page,index) => `<label class="check-label"><input type="checkbox" data-ai-page="${index}" ${ui.selected.has(page.url) ? 'checked' : ''}><span>${escape(pagePath(page.url))}</span></label>`).join('')}</div>
      ${!pages.length ? '<p class="small-note">No page content was available. Run a new Python scan first.</p>' : ''}
      <label class="check-label ai-image-option"><input type="checkbox" id="ai-images" ${ui.images ? 'checked' : ''} ${enabled(previews && !ui.deepBusy)}>Include desktop and mobile screenshots</label>
      ${!previews ? '<p class="small-note">Enable browser previews in a new scan to add visual evidence.</p>' : ''}</div>
      <div class="ai-review-actions"><span class="label">YOUR REVIEW CONTROLS</span><label class="check-label"><input type="checkbox" id="ai-consent" ${ui.consent ? 'checked' : ''}><span>Allow selected page content, scan evidence and chosen screenshots to be sent to OpenAI.</span></label>
        <p class="small-note">One request per uncached page, up to 1,800 output tokens each. Saved reviews are reused. AI suggestions need your judgment.</p>
        <button class="button primary" data-ai-action="deep" ${enabled(ready() && ui.consent && ui.selected.size && !ui.deepBusy && !ui.busy)}>RUN DEEP AI REVIEW</button>
        ${ui.deepBusy ? '<button class="button small" data-ai-action="stop-deep">STOP AFTER THIS PAGE</button>' : ''}
        <p id="ai-review-progress" class="ai-run-status" role="status" aria-live="polite">${escape(ui.status)}</p>
        <p id="ai-review-error" class="ai-error" role="alert">${escape(ui.deepError)}</p>
        <button class="button" data-ai-action="summary" ${enabled(ready() && ui.consent && !ui.deepBusy && !ui.busy)}>ANALYZE WITH AI</button>
        <p class="small-note">A summary explains the scanner's existing findings. Deep review inspects page content for additional concerns.</p></div></div>
    ${ui.reviews.summary ? `<div class="ai-summary"><h3>Python findings, explained.</h3>${plainResult(ui.reviews.summary)}</div>` : ''}
    <div class="ai-results"><div class="section-heading"><h2>Additional AI observations.</h2><span class="label">KEPT SEPARATE FROM PYTHON FINDINGS</span></div>
      ${results.length ? `<div class="ai-result-toolbar"><label for="ai-page-result" class="label">READ A SAVED PAGE REVIEW</label><select id="ai-page-result" class="control">${results.map((result,index) => `<option value="${index}">${escape(pagePath(result.page))}${result.include_images ? ' / WITH PREVIEWS' : ' / SOURCE REVIEW'}</option>`).join('')}</select><button class="button small" data-ai-action="download">DOWNLOAD AI REVIEWS</button></div><div id="ai-page-output">${pageResult(results[results.length-1])}</div>` : '<p class="small-note">Completed page reviews will appear here with evidence and a proposed verification step.</p>'}</div></section>`;
}

function renderReview() {
  const body = main.querySelector('.report-body');
  const [view,id,tab] = location.hash.replace(/^#/,'').split('/');
  if (!body || view !== 'report' || id !== ui.scan?.id || tab !== 'ai') return;
  const active = body.contains(document.activeElement) ? document.activeElement : null;
  const focusId = active?.id, focusPage = active?.dataset.aiPage;
  body.innerHTML = reviewView();
  const select = body.querySelector('#ai-page-result');
  if (select) select.value = String(ui.reviews.deep_reviews.length-1);
  const target = focusId ? document.getElementById(focusId) : focusPage !== undefined ? body.querySelector(`[data-ai-page="${focusPage}"]`) : null;
  if (target && !target.disabled) target.focus({preventScroll:true});
}

function chatMarkup() {
  const scan = ui.scan;
  return `<button class="ai-chat-launcher" aria-controls="lintel-chat" aria-expanded="${ui.open}" data-ai-action="toggle-chat"><svg viewBox="0 0 120 120" aria-hidden="true"><use href="#cube"/></svg><span>ASK LINTEL</span></button>
    <section id="lintel-chat" class="ai-chat-panel" role="dialog" aria-labelledby="lintel-chat-title" ${ui.open ? '' : 'hidden'}>
      <div class="ai-chat-head"><div><span class="label">YOUR WEBSITE ASSISTANT</span><h2 id="lintel-chat-title">Ask Lintel</h2></div><button class="icon-button" data-ai-action="close-chat" aria-label="Close Ask Lintel">×</button></div>
      <div class="ai-chat-context"><span class="label">${scan ? escape(new URL(scan.url).host) : 'NO SCAN SELECTED'}</span><p>Ask about findings, priorities or how to verify a fix.</p></div>
      <div class="ai-chat-log" role="log" aria-live="polite" aria-label="Chat messages">${chatHistory().map(item => `<div class="ai-chat-message ${item.role}"><span class="label">${item.role === 'user' ? 'YOU' : 'LINTEL'}</span>${paragraph(item.content)}</div>`).join('')}
        ${!chatHistory().length ? '<p class="ai-chat-empty">What should I fix first?<br>What does this finding mean?<br>How can I check the proposed fix?</p>' : ''}
        ${ui.busy ? '<p class="small-note" role="status">Reading your saved scan…</p>' : ''}</div>
      <div class="ai-chat-bottom">${!ready() ? '<p class="ai-chat-note">Add your API key in workspace setup to enable chat.</p>' : ''}${!scan ? '<p class="ai-chat-note">Run a scan or open one from History to begin.</p>' : ''}
        <label class="check-label"><input type="checkbox" id="ai-chat-consent" ${ui.chatConsent.has(scan?.id) ? 'checked' : ''}><span>Allow my messages and limited scan context to be sent to OpenAI.</span></label>
        <form id="lintel-chat-form"><label for="lintel-question" class="sr-only">Ask about the selected website</label><textarea id="lintel-question" name="question" placeholder="Ask about this website…" rows="2" maxlength="2000" ${enabled(ready() && scan && !ui.busy && !ui.deepBusy)}>${escape(ui.drafts.get(scan?.id) || '')}</textarea><button class="button primary" type="submit" ${enabled(ready() && scan && ui.chatConsent.has(scan?.id) && !ui.busy && !ui.deepBusy)}>SEND</button></form>
        <p class="ai-error" role="alert">${escape(ui.error)}</p><div class="ai-chat-foot"><span>ONE API REQUEST PER MESSAGE</span><button class="text-button" data-ai-action="clear-chat" ${enabled(!ui.busy)}>CLEAR CHAT</button></div></div></section>`;
}

function renderChat() {
  if (!ui.config) return;
  const focusId = root.contains(document.activeElement) ? document.activeElement.id : '';
  root.innerHTML = chatMarkup();
  const log = root.querySelector('.ai-chat-log');
  if (log) log.scrollTop = log.scrollHeight;
  const target = focusId ? document.getElementById(focusId) : null;
  if (target && !target.disabled) target.focus({preventScroll:true});
}

async function ensureContext(id) {
  if (!id || id === ui.scan?.id || id === contextPending) return;
  contextPending = id;
  const token = ++ui.token;
  try {
    const [saved,reviews] = await Promise.all([request('/api/scan/'+encodeURIComponent(id)),request('/api/ai/reviews?id='+encodeURIComponent(id))]);
    if (token !== ui.token) return;
    ui.scan = saved.scan; ui.reviews = reviews; ui.selected = new Set(saved.scan.pages.slice(0,30).map(page => page.url));
    ui.consent = false; ui.images = false; ui.error = ''; ui.deepError = ''; ui.status = '';
    renderChat(); enhance();
  } catch(error) {ui.error = error.message; renderChat();}
  finally {if (contextPending === id) contextPending = '';}
}

async function initializeAI() {
  if (configPending || ui.config) return;
  configPending = true;
  try {
    ui.config = await request('/api/ai/status');
    renderChat();
    const boot = await request('/api/boot');
    const [view,id] = location.hash.replace(/^#/,'').split('/');
    await ensureContext(view === 'report' ? id : boot.scans[0]?.id);
    enhance();
  } catch { /* The existing password screen retains ownership of authentication. */ }
  finally {configPending = false;}
}

function enhance() {
  if (!ui.config) {initializeAI(); return;}
  const [view,id,tab] = location.hash.replace(/^#/,'').split('/');
  if (view === 'report' && id !== ui.scan?.id) {ensureContext(id); return;}
  const nav = main.querySelector('.report-tabs');
  if (nav && !nav.querySelector('[data-lintel-tab]')) {
    const button = document.createElement('button');
    button.textContent = 'AI REVIEW'; button.dataset.action = 'tab-ai'; button.dataset.lintelTab = 'true';
    nav.append(button);
  }
  if (nav) {
    const button = nav.querySelector('[data-lintel-tab]');
    button?.classList.toggle('active',tab === 'ai'); button?.setAttribute('aria-current',tab === 'ai' ? 'page' : 'false');
  }
  if (tab === 'ai' && ui.scan && !main.querySelector(`[data-lintel-view="${ui.scan.id}"]`)) renderReview();
  const panel = main.querySelector('#finding-detail');
  const selected = main.querySelector('.finding-row.selected')?.dataset.value;
  if (panel && selected && !panel.querySelector('[data-lintel-issue]')) {
    const result = (ui.reviews.issue_reviews || []).find(item => item.issue_id === selected);
    const wrapper = document.createElement('div'); wrapper.className = 'detail-section ai-issue'; wrapper.dataset.lintelIssue = selected;
    wrapper.innerHTML = `<span class="label">A CLEARER EXPLANATION</span><label class="check-label"><input type="checkbox" data-ai-issue-consent="${escape(selected)}" ${ui.issueConsent.has(selected) ? 'checked' : ''}><span>Allow this finding to be sent to OpenAI.</span></label><button class="button small" data-ai-action="issue" data-issue="${escape(selected)}" ${enabled(ready() && ui.issueConsent.has(selected) && !ui.busy && !ui.deepBusy)}>EXPLAIN WITH AI</button><div class="ai-issue-output">${plainResult(result)}</div>`;
    panel.append(wrapper);
  }
  const settings = main.querySelector('#settings-form');
  if (settings && !settings.querySelector('[data-lintel-setup]')) {
    const section = document.createElement('div'); section.className = 'metadata ai-setup'; section.dataset.lintelSetup = 'true';
    section.innerHTML = `<span class="label">OPTIONAL AI / SERVER CONFIGURATION</span><h3>Connect the context.</h3><p>${ready() ? 'API key configured. ' : 'API key not configured. '}${escape(ui.config.model)}</p><p>Keep the key on your computer or server. The browser never receives it.</p><code>OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"</code><p class="small-note">Add it to .streamlit/secrets.toml or the server environment, install requirements.txt, then restart python server.py.</p>`;
    settings.children[1]?.append(section);
  }
}

async function loadReviews() {
  const scanId = ui.scan?.id;
  if (!scanId) return;
  try {
    const reviews = await request('/api/ai/reviews?id='+encodeURIComponent(scanId));
    if (ui.scan?.id === scanId) ui.reviews = reviews;
  } catch (error) {ui.deepError = error.message;}
}

async function runDeep() {
  if (!ready() || !ui.consent || ui.deepBusy || ui.busy) return;
  const scan = ui.scan, selected = [...ui.selected], images = ui.images;
  ui.deepBusy = true; ui.stop = false; ui.deepError = '';
  ui.status = `Starting review of ${selected.length} selected page(s).`; renderReview(); renderChat();
  let finished = 0;
  try {
    for (const page of selected) {
      if (ui.stop) break;
      ui.status = `Reviewing ${finished+1} of ${selected.length}: ${pagePath(page)}`;
      if (ui.scan?.id === scan.id) renderReview();
      await request('/api/ai/page',{scan_id:scan.id,page,include_images:images,consent:true});
      finished++;
    }
    ui.status = `${finished} of ${selected.length} page reviews completed or reused.${ui.stop ? ' Stopped after the current page.' : ''}`;
  } catch(error) {ui.deepError = error.message; ui.status = `${finished} page review(s) completed. Saved reviews will be reused on the next run.`;}
  finally {ui.deepBusy = false; if (ui.scan?.id === scan.id) {await loadReviews(); renderReview(); renderChat();}}
}

async function runSummary() {
  if (!ready() || !ui.consent || ui.deepBusy || ui.busy) return;
  const scanId = ui.scan.id; ui.busy = true; ui.deepError = ''; ui.status = 'Explaining the Python findings…'; renderReview(); renderChat();
  try {await request('/api/ai/summary',{scan_id:scanId,consent:true}); ui.status = 'Summary ready.';}
  catch(error) {ui.deepError = error.message;}
  finally {ui.busy = false; if (ui.scan?.id === scanId) {await loadReviews(); renderReview(); renderChat();}}
}

function downloadReviews() {
  const lines = ['LINTEL / AI REVIEW',ui.scan.url,''];
  if (ui.reviews.summary) lines.push(ui.reviews.summary.text,'');
  for (const result of ui.reviews.deep_reviews) {
    lines.push(result.page,result.summary,'');
    for (const item of result.findings) lines.push(item.title,`AI suggestion / ${item.priority}`,`Evidence: ${item.quote}`,item.evidence_status,`Proposed fix: ${item.suggested_fix}`,`How to verify: ${item.verification}`,'');
    lines.push('Manual checks',...result.manual_checks,'');
  }
  const url = URL.createObjectURL(new Blob([lines.join('\n')],{type:'text/plain'}));
  const link = document.createElement('a'); link.href = url; link.download = 'lintel-ai-reviews.txt'; link.click();
  setTimeout(() => URL.revokeObjectURL(url),1000);
}

document.addEventListener('click', async event => {
  const button = event.target.closest('[data-ai-action]');
  if (!button || button.disabled) return;
  const action = button.dataset.aiAction;
  if (['toggle-chat','close-chat'].includes(action)) {
    ui.open = action === 'toggle-chat' ? !ui.open : false; renderChat();
    if (ui.open) root.querySelector('#lintel-question')?.focus(); else root.querySelector('.ai-chat-launcher')?.focus();
  }
  if (action === 'clear-chat' && ui.scan && !ui.busy) {ui.chats.set(ui.scan.id,[]); saveChats(); ui.error = ''; renderChat();}
  if (action === 'stop-deep') {ui.stop = true; button.disabled = true; button.textContent = 'STOPPING AFTER THIS PAGE';}
  if (action === 'deep') await runDeep();
  if (action === 'summary') await runSummary();
  if (action === 'download') downloadReviews();
  if (action === 'issue' && ui.issueConsent.has(button.dataset.issue) && !ui.busy && !ui.deepBusy) {
    const scanId = ui.scan.id; button.disabled = true; button.textContent = 'EXPLAINING…'; ui.busy = true; renderChat();
    try {
      const response = await request('/api/ai/issue',{scan_id:scanId,issue_id:button.dataset.issue,consent:true});
      if (button.isConnected) button.closest('.ai-issue').querySelector('.ai-issue-output').innerHTML = plainResult(response.result);
      if (ui.scan?.id === scanId) await loadReviews();
    } catch(error) {if (button.isConnected) button.closest('.ai-issue').querySelector('.ai-issue-output').textContent = error.message;}
    finally {ui.busy = false; if (button.isConnected) {button.disabled = false; button.textContent = 'EXPLAIN WITH AI';} renderChat();}
  }
});

document.addEventListener('change', event => {
  const target = event.target;
  if (target.dataset.aiPage !== undefined) {const page = ui.scan.pages[Number(target.dataset.aiPage)]?.url; if (page) target.checked ? ui.selected.add(page) : ui.selected.delete(page); renderReview();}
  if (target.id === 'ai-images') ui.images = target.checked;
  if (target.id === 'ai-consent') {ui.consent = target.checked; renderReview();}
  if (target.id === 'ai-chat-consent') {target.checked ? ui.chatConsent.add(ui.scan?.id) : ui.chatConsent.delete(ui.scan?.id); renderChat();}
  if (target.dataset.aiIssueConsent) {const id = target.dataset.aiIssueConsent; target.checked ? ui.issueConsent.add(id) : ui.issueConsent.delete(id); target.closest('.ai-issue').querySelector('button').disabled = !ready() || !target.checked || ui.busy || ui.deepBusy;}
  if (target.id === 'ai-page-result') main.querySelector('#ai-page-output').innerHTML = pageResult(ui.reviews.deep_reviews[Number(target.value)]);
});
document.addEventListener('input', event => {if (event.target.id === 'lintel-question') ui.drafts.set(ui.scan?.id,event.target.value);});
document.addEventListener('keydown', event => {if (event.key === 'Escape' && ui.open && !document.querySelector('#drawer')?.open) {ui.open = false; renderChat(); root.querySelector('.ai-chat-launcher')?.focus();}});
document.addEventListener('submit', async event => {
  if (event.target.id !== 'lintel-chat-form') return;
  event.preventDefault();
  if (!ready() || !ui.scan || !ui.chatConsent.has(ui.scan.id) || ui.busy || ui.deepBusy) return;
  const question = String(new FormData(event.target).get('question') || '').trim(); if (!question) return;
  const scanId = ui.scan.id, history = [...chatHistory()]; ui.busy = true; ui.error = ''; ui.drafts.set(scanId,'');
  ui.chats.set(scanId,[...history,{role:'user',content:question}]); renderChat();
  try {
    const response = await request('/api/ai/chat',{scan_id:scanId,question,history:history.slice(-12),consent:true});
    ui.chats.set(scanId,[...history,{role:'user',content:question},{role:'assistant',content:response.result.text}].slice(-24)); saveChats();
  } catch(error) {ui.chats.set(scanId,history); saveChats(); ui.drafts.set(scanId,question); ui.error = error.message;}
  finally {ui.busy = false; renderChat(); root.querySelector('#lintel-question')?.focus();}
});

new MutationObserver(() => {if (!observed) {observed = true; queueMicrotask(() => {observed = false; enhance();});}}).observe(main,{childList:true,subtree:true});
window.addEventListener('hashchange',() => {const [view,id] = location.hash.replace(/^#/,'').split('/'); if (ui.deepBusy && view === 'report' && id !== ui.scan?.id) ui.stop = true; enhance();});
initializeAI();
