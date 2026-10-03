import {effectState, executionNote} from './presentation.js';
const $ = id => document.getElementById(id);
let token = '', selected = null, pending = null, epoch = 0, refreshing = false;
const starts = new Map(), decisions = new Map();
function message(text = '') { $('message').textContent = text; }
async function api(path, options = {}) {
  const response = await fetch('/api/v1' + path, {cache:'no-store', ...options,
    headers: {'Authorization':'Bearer ' + token, 'Content-Type':'application/json', ...options.headers}});
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.message || data.detail || `Request failed (${response.status})`);
  }
  return response.status === 202 || response.status === 204 ? null : response.json();
}
function clearSession() {
  epoch++; token = ''; selected = null; pending = null; starts.clear(); decisions.clear();
  $('workspace').hidden = true; $('connection').hidden = false; $('disconnect').hidden = true;
  $('api-key').value = ''; $('case-list').replaceChildren(); $('case-detail').hidden = true;
  $('session-label').textContent = 'Disconnected'; message();
}
function text(id, value) { $(id).textContent = String(value); }
function node(tag, value, className) {
  const element = document.createElement(tag); element.textContent = value;
  if (className) element.className = className; return element;
}
async function loadCases() {
  const current = epoch;
  const runs = await api('/workflow-runs?limit=20&exclude_evaluations=true');
  const summaries = await Promise.all(runs.items.filter(r => r.workflow_type === 'vendor_onboarding')
    .slice(0,8).map(r => api(`/workflow-runs/${r.id}/business-evidence`)));
  if (current !== epoch) return;
  $('case-list').replaceChildren();
  if (!summaries.length) $('case-list').append(node('p','No supplier cases yet. Start an onboarding request.','empty'));
  for (const summary of summaries) {
    const button = node('button','', 'case-row' + (summary.run_id === selected ? ' selected' : ''));
    button.type = 'button'; button.dataset.runId = summary.run_id;
    const name = node('span', summary.supplier_name); name.append(node('small', 'Supplier onboarding · ' + summary.jurisdiction));
    button.append(name, node('span', summary.business_status.replaceAll('_',' '), 'pill'));
    button.addEventListener('click', async () => {
      selected = summary.run_id; pending = null; $('approval-form').hidden = true;
      $('case-detail').hidden = true; await refresh();
    });
    $('case-list').append(button);
  }
}
async function loadSelected() {
  if (!selected) return;
  const runId = selected, current = epoch;
  const [summary, approvals, evidence] = await Promise.all([
    api(`/workflow-runs/${runId}/business-evidence`), api(`/workflow-runs/${runId}/approvals`),
    api(`/workflow-runs/${runId}/evidence`)]);
  if (current !== epoch || runId !== selected) return;
  $('case-detail').hidden = false;
  text('case-title',summary.supplier_name); text('case-identity', 'CASE / ' + summary.run_id);
  text('case-status',summary.business_status.replaceAll('_',' '));
  text('decision-count',summary.effects.approval_decisions); text('supplier-count',summary.effects.approved_suppliers);
  text('notification-count',summary.effects.synthetic_notifications);
  text('attempt-count',summary.decision_activity_attempt ?? '—');
  text('business-state',effectState(summary)); text('execution-state',summary.temporal_status);
  text('execution-note',executionNote(summary)); text('risk-score','Risk score / ' + (summary.risk_score ?? 'Pending'));
  pending = approvals.find(a => a.status === 'pending') || null;
  $('approval-form').hidden = !pending;
  text('approval-summary',pending ? 'Pending approval · request version ' + pending.version + '. ' + pending.summary :
    approvals.length ? 'Decision recorded. No pending approval remains.' : 'Policy assessment is preparing the approval request.');
  const model = evidence.model_calls.at(-1);
  text('model-explanation',model?.explanation_summary || (model ? 'No valid explanation is available. Human authority is unchanged.' : 'Awaiting assessment.'));
  text('model-provider',model ? `${model.provider} · ${model.model} · ${model.status} · advisory only` : 'No model result recorded');
  $('timeline').replaceChildren();
  for (const event of evidence.events) {
    const item = node('li',''); item.append(node('strong',event.event_type.replaceAll('.',' / ').replaceAll('_',' ')),node('small',event.summary));
    if (event.event_type === 'risk.policy_context' && Array.isArray(event.payload?.citations)) {
      const sources = event.payload.citations.map(c => c.title + ' · ' + c.source_uri).join(' / ');
      if (sources) item.append(node('div','Sources: ' + sources,'hint'));
    }
    $('timeline').append(item);
  }
  text('evidence-freshness','Observed ' + new Date(summary.observed_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}));
}
async function refresh() {
  if (!token || refreshing) return;
  refreshing = true;
  try { await loadCases(); await loadSelected(); message(); }
  catch (error) { message('Evidence could not be refreshed: ' + error.message + '. Previously shown values may be stale.'); }
  finally { refreshing = false; }
}
$('connect-form').addEventListener('submit',async event => {
  event.preventDefault(); token = $('api-key').value.trim(); epoch++;
  try {
    await api('/workflow-runs?limit=1'); $('api-key').value = '';
    $('connection').hidden = true; $('workspace').hidden = false; $('disconnect').hidden = false;
    text('session-label','Operator connected'); await refresh();
  } catch (error) { token = ''; message(error.message); }
});
$('disconnect').addEventListener('click',clearSession);
$('refresh').addEventListener('click',refresh);
$('reference').value = 'orion-' + Date.now().toString(36);
$('supplier-form').addEventListener('submit',async event => {
  event.preventDefault(); $('start-case').disabled = true;
  const current = epoch;
  try {
    const payload = {external_reference:$('reference').value.trim(), legal_name:$('supplier-name').value.trim(),
      jurisdiction:$('jurisdiction').value, contact_email:$('supplier-email').value.trim()};
    const fingerprint = JSON.stringify(payload);
    let draft = starts.get(fingerprint);
    if (!draft) { draft = {idempotency_key:'console-start-' + crypto.randomUUID(), vendor:null}; starts.set(fingerprint,draft); }
    if (!draft.vendor) draft.vendor = await api('/vendors',{method:'POST',body:JSON.stringify(payload)});
    if (current !== epoch) return;
    const run = await api(`/vendors/${draft.vendor.id}/onboarding`,{method:'POST',body:JSON.stringify({idempotency_key:draft.idempotency_key})});
    if (current !== epoch) return;
    selected = run.id; pending = null; $('approval-form').hidden = true;
    await refresh(); $('case-detail').scrollIntoView({behavior:'smooth',block:'start'});
  } catch (error) { message(error.message + '. Retrying the same form reuses its workflow-start key.'); }
  finally { $('start-case').disabled = false; }
});
$('approval-form').addEventListener('submit',async event => {
  event.preventDefault(); if (!pending || !selected) return;
  const decision = event.submitter?.value;
  if (!['approved','rejected'].includes(decision)) return;
  const payload = {approval_request_id:pending.id,expected_version:pending.version,decision,rationale:$('rationale').value.trim()};
  const fingerprint = JSON.stringify(payload);
  if (!decisions.has(fingerprint)) decisions.set(fingerprint,'console-decision-' + crypto.randomUUID());
  payload.idempotency_key = decisions.get(fingerprint);
  $('approve').disabled = $('reject').disabled = true;
  try { await api(`/workflow-runs/${selected}/approval`,{method:'POST',body:JSON.stringify(payload)}); await refresh(); }
  catch(error) { message(error.message + '. An uncertain response does not prove the decision failed.'); }
  finally { $('approve').disabled = $('reject').disabled = false; }
});
setInterval(() => { if (!document.hidden && token) refresh(); },2500);
window.addEventListener('pagehide',clearSession);
