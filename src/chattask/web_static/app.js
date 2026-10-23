const $ = (id) => document.getElementById(id);
const once = $('submit-task');
const open = $('open-session');
const close = $('close-session');
const send = $('send-command');
let sessionId = null;
let busy = false;
let csrfToken = null;
let authenticated = false;

async function request(path, method = 'GET', body) {
  const headers = body ? {'Content-Type': 'application/json'} : {};
  if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken;
  const response = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    redirect: 'error',
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store'
  });
  if (response.status === 401) {
    authenticated = false; csrfToken = null; sessionId = null;
    clearTemplateState();
    $('task-output').textContent = '';
    $('session-output').textContent = '';
    $('command').value = '';
    controls(false);
    location.replace('/auth/?next=/');
    throw new Error('登录已失效，请重新登录。');
  }
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `HTTP ${response.status}`);
  return payload;
}
function show(id, text, error = false) {
  $(id).textContent = text;
  $(id).classList.toggle('error', error);
}
function controls(active, working = false) {
  once.disabled = !authenticated || working;
  open.disabled = !authenticated || active || working;
  close.disabled = !authenticated || !active || working;
  send.disabled = !authenticated || !active || working;
  $('command').disabled = !authenticated || !active || working;
  $('logout').disabled = working;
}
function pretty(x) { return JSON.stringify(x, null, 2); }

once.addEventListener('click', async () => {
  const raw = $('numbers').value.split(',').map(x => x.trim());
  if (!raw.length || raw.length > 8 || raw.some(x => !/^-?\d+$/.test(x))) {
    show('task-state', '请输入 1–8 个整数，用逗号分隔。', true);
    return;
  }
  const values = raw.map(Number);
  if (values.some(x => !Number.isSafeInteger(x) || Math.abs(x) > 10000)) {
    show('task-state', '每个数须在 -10000 至 10000 之间。', true);
    return;
  }
  busy = true; controls(!!sessionId, true);
  show('task-state', '正在创建 Sandbox、执行测试并回收…');
  try {
    const task = await request('/api/tasks', 'POST', {values});
    show('task-state', task.passed && task.cleanup.container_absent ? '完成 · 测试通过 · Sandbox 已销毁' : '任务结束，但仍需检查');
    show('task-output', pretty({task_id: task.task_id, sandbox_id: task.sandbox_id, result: task.result,
      program_exit_code: task.program.exit_code, test_exit_code: task.tests.exit_code,
      test_output: task.tests.stdout || task.tests.stderr, cleanup: task.cleanup, runtime: task.runtime}));
  } catch (e) {
    show('task-state', e.message, true);
  } finally { busy = false; controls(!!sessionId); }
});
open.addEventListener('click', async () => {
  busy = true; controls(false, true);
  show('session-state', '正在创建持续 Sandbox…');
  try {
    const session = await request('/api/sessions', 'POST');
    sessionId = session.id;
    show('session-state', `活动中 · Sandbox ${session.sandbox_id.slice(0, 8)}… · 最多 5 分钟`);
    show('session-output', `session_id: ${session.id}\nsandbox_id: ${session.sandbox_id}\n运行时: ${session.runtime.runtime}\n可以继续发送命令。`);
  } catch (e) { show('session-state', e.message, true); }
  finally { busy = false; controls(!!sessionId); }
});
send.addEventListener('click', async () => {
  if (!sessionId) return;
  const command = $('command').value.trim();
  if (!command || command.length > 256) return show('session-state', '输入 1–256 字符的命令。', true);
  busy = true; controls(true, true); show('session-state', '正在运行命令…');
  try {
    const response = await request(`/api/sessions/${sessionId}/commands`, 'POST', {command});
    const result = response.result;
    $('session-output').textContent += `\n\n$ ${command}\n${result.stdout || '(无标准输出)'}${result.stderr ? '\n[stderr] ' + result.stderr : ''}\n[exit=${result.exit_code}]`;
    show('session-state', `活动中 · 上条命令退出码 ${result.exit_code}`);
  } catch (e) { show('session-state', e.message, true); }
  finally { busy = false; controls(!!sessionId); }
});
$('command').addEventListener('keydown', e => { if (e.key === 'Enter' && !send.disabled) send.click(); });
close.addEventListener('click', async () => {
  if (!sessionId) return;
  busy = true; controls(true, true);
  show('session-state', '正在销毁 Sandbox…');
  try {
    const response = await request(`/api/sessions/${sessionId}`, 'DELETE');
    show('session-state', response.deleted ? '已销毁 · Sandbox 不再存在' : '清理尚未确认');
    $('session-output').textContent += `\n\n[destroy] ${response.sandbox_id}`;
    sessionId = null;
  } catch (e) { show('session-state', e.message, true); }
  finally { busy = false; controls(!!sessionId); }
});
document.querySelectorAll('[data-command]').forEach(button => button.addEventListener('click', () => {
  if (sessionId && !busy) { $('command').value = button.dataset.command; $('command').focus(); }
}));
$('logout').addEventListener('click', async () => {
  if (busy) return;
  try {
    if (sessionId) await request(`/api/sessions/${sessionId}`, 'DELETE');
    await request('/auth/logout', 'POST');
    authenticated = false; csrfToken = null; sessionId = null;
    clearTemplateState();
    $('task-output').textContent = ''; $('session-output').textContent = ''; $('command').value = '';
    controls(false);
    location.replace('/auth/?next=/');
  } catch (e) { show('session-state', e.message, true); }
});
controls(false);
(async () => {
  try {
    const state = await request('/auth/session');
    if (!state.authenticated) return location.replace('/auth/?next=/');
    authenticated = true; csrfToken = state.csrf_token;
    $('account-state').textContent = state.user.display_name || '已登录';
    controls(false);
    await initializeTemplates();
  } catch (e) { show('task-state', '无法确认登录状态，请刷新后重试。', true); } deployTemplateControls();
})();

// Template workflow. Every private value is rendered through textContent.
let templates = [];
let repositories = [];
let selectedRun = null;
let templateBusy = false;
let pendingCreate = null;
let refreshTimer = null;
const activeStatuses = new Set(['queued', 'running', 'cancel_requested', 'releasing']);

function clearTemplateState() {
  templates = []; repositories = []; selectedRun = null; pendingCreate = null;
  sessionStorage.removeItem('chattask-pending-run');
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = null;
  $('template-picker').replaceChildren(); $('repository-picker').replaceChildren(); $('run-history').replaceChildren();
  $('template-description').textContent = '';
  $('run-summary').replaceChildren(); $('run-steps').replaceChildren(); $('artifact-list').replaceChildren();
  $('run-state').textContent = ''; $('run-error').textContent = ''; $('tool-state').textContent = '';
  $('approval-confirmation').value = ''; $('approval-box').hidden = true; $('cancel-run').hidden = true;
  deployTemplateControls();
}
function deployTemplateControls() {
  $('create-run').disabled = !authenticated || templateBusy || !templates.length || !repositories.length;
  $('save-template').disabled = !authenticated || templateBusy;
  $('refresh-run').disabled = !authenticated || templateBusy;
  $('approve-run').disabled = !authenticated || templateBusy || !selectedRun ||
    selectedRun.status !== 'awaiting_approval' || !selectedRun.pull_request ||
    $('approval-confirmation').value !== approvalPhrase(selectedRun);
  $('cancel-run').disabled = !authenticated || templateBusy || !selectedRun ||
    !['queued', 'running'].includes(selectedRun.status);
}
function option(value, label) {
  const element = document.createElement('option');
  element.value = value; element.textContent = label;
  return element;
}
function approvalPhrase(run) { return `TAG ${run.github_repo} v${run.version}`; }
function selectTab(key) {
  $('template-view').hidden = key !== 'templates';
  $('sandbox-view').hidden = key !== 'sandbox';
  $('templates-tab').setAttribute('aria-selected', String(key === 'templates'));
  $('sandbox-tab').setAttribute('aria-selected', String(key === 'sandbox'));
}
$('templates-tab').addEventListener('click', () => selectTab('templates'));
$('sandbox-tab').addEventListener('click', () => selectTab('sandbox'));

async function initializeTemplates() {
  try {
    const [catalog, sources, history] = await Promise.all([
      request('/api/templates'), request('/api/repositories'), request('/api/runs')]);
    templates = catalog.templates;
    repositories = sources.repositories;
    const picker = $('template-picker'); picker.replaceChildren();
    templates.forEach(t => picker.append(option(t.id, `${t.name} · v${t.revision}${t.built_in ? ' · 内置' : ''}`)));
    const repos = $('repository-picker'); repos.replaceChildren();
    repositories.forEach(r => repos.append(option(r.id, `${r.id} · ${r.github_repo}`)));
    renderTemplateSelection();
    renderHistory(history.runs);
    const stored = sessionStorage.getItem('chattask-pending-run');
    if (stored) {
      try { pendingCreate = JSON.parse(stored); } catch (_) { sessionStorage.removeItem('chattask-pending-run'); }
    }
    if (pendingCreate) {
      const found = history.runs.find(r => r.request_id === pendingCreate.request_id);
      if (found) { pendingCreate = null; sessionStorage.removeItem('chattask-pending-run'); await loadRun(found.id); }
      else show('create-state', '上次提交结果尚未确认。再次点击会使用同一请求 ID 核对或重试。', true);
    } else if (history.runs.length) await loadRun(history.runs[0].id);
    if (!repositories.length) show('create-state', '尚无受信仓库。请管理员在服务器 CLI 中登记干净的 Git 根目录。', true);
    deployTemplateControls();
    const connection = await request('/api/tool-connections/status');
    show('tool-state', connection.available ? `ChatPyPI 工具可用：${(connection.tools || []).join(', ')}` :
      `ChatPyPI 工具尚不可用：${connection.reason || '连接未就绪'}`, !connection.available);
  } catch (error) {
    show('create-state', `模板资料暂不可用：${error.message}。现有画面可能已过期。`, true);
  }
}
function renderTemplateSelection() {
  const selected = templates.find(t => t.id === $('template-picker').value);
  if (!selected) return;
  $('template-description').textContent = selected.description || '此模板没有附加说明。';
  if (!selected.built_in) {
    $('custom-id').value = selected.id; $('custom-name').value = selected.name;
    $('custom-description').value = selected.description;
    const kinds = new Set(selected.steps.map(s => s.kind));
    for (const kind of ['probe', 'test', 'build', 'check']) $(`step-${kind}`).checked = kinds.has(kind);
    $('test-suite').value = selected.steps.find(s => s.kind === 'test')?.suite || 'pytest';
    $('save-template').textContent = `保存新修订 · v${selected.revision + 1}`;
  } else {
    $('custom-id').value = ''; $('custom-name').value = ''; $('custom-description').value = '';
    for (const kind of ['probe', 'test', 'build', 'check']) $(`step-${kind}`).checked = true;
    $('test-suite').value = 'pytest';
    $('save-template').textContent = '保存自定义模板';
  }
}
$('template-picker').addEventListener('change', renderTemplateSelection);
$('approval-confirmation').addEventListener('input', deployTemplateControls);
$('run-history').addEventListener('change', () => { if ($('run-history').value) loadRun($('run-history').value); });

async function saveTemplate() {
  const id = $('custom-id').value.trim();
  const steps = [];
  for (const kind of ['probe', 'test', 'build', 'check']) {
    if ($(`step-${kind}`).checked) steps.push(kind === 'test' ? {kind, suite: $('test-suite').value} : {kind});
  }
  const body = {id, name: $('custom-name').value.trim(), description: $('custom-description').value.trim(), steps};
  const existing = templates.find(t => t.id === id);
  if (existing?.built_in) return show('template-state', '内置模板不可修改，请使用新的模板 ID。', true);
  templateBusy = true; deployTemplateControls();
  try {
    const saved = await request(existing ? `/api/templates/${encodeURIComponent(id)}` : '/api/templates',
                                existing ? 'PUT' : 'POST', body);
    const old = templates.findIndex(t => t.id === id);
    if (old >= 0) templates[old] = saved; else templates.push(saved);
    const picker = $('template-picker'); picker.replaceChildren();
    templates.forEach(t => picker.append(option(t.id, `${t.name} · v${t.revision}${t.built_in ? ' · 内置' : ''}`)));
    picker.value = saved.id; renderTemplateSelection();
    show('template-state', `已保存 ${saved.name} · 修订 v${saved.revision}`);
  } catch (error) { show('template-state', error.message, true); }
  finally { templateBusy = false; deployTemplateControls(); }
}
$('save-template').addEventListener('click', saveTemplate);

async function refreshHistory() {
  const history = await request('/api/runs');
  renderHistory(history.runs);
  return history.runs;
}
function renderHistory(runs) {
  const list = $('run-history');
  const current = selectedRun?.id || list.value;
  list.replaceChildren(option('', '选择任务'));
  runs.forEach(r => list.append(option(r.id, `${r.version} · ${r.repository_id} · ${r.status} · ${r.id.slice(0, 8)}`)));
  if (runs.some(r => r.id === current)) list.value = current;
}
async function loadRun(id) {
  try {
    const run = await request(`/api/runs/${encodeURIComponent(id)}`);
    selectedRun = run;
    $('run-history').value = id;
    renderRun(run);
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = activeStatuses.has(run.status) ? setTimeout(() => loadRun(run.id), 3000) : null;
  } catch (error) {
    show('run-error', `无法刷新任务：${error.message}。当前展示的是上次成功读取的状态。`, true);
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = selectedRun && activeStatuses.has(selectedRun.status) ? setTimeout(() => loadRun(selectedRun.id), 7000) : null;
  }
}
function renderRun(run) {
  const labels = {queued: '排队中', running: '运行中', cancel_requested: '取消请求已记录，等待安全检查点',
    cancelled: '已取消且 Sandbox 清理已核验', preflight_passed: '预检通过；该自定义步骤组合不满足发布条件', awaiting_approval: '预检通过，等待明确批准',
    awaiting_publication: '标签目标已核验，等待独立验证 OIDC 工作流和 PyPI 产物',
    blocked: '已阻塞', interrupted: '进程中断，需人工核对', release_unknown: '发布写入结果未知，需核对远端目标'};
  show('run-state', `${labels[run.status] || run.status} · ${run.id.slice(0, 8)}`,
    ['blocked', 'interrupted', 'release_unknown'].includes(run.status));
  const summary = $('run-summary'); summary.replaceChildren();
  const lines = [
    `仓库 ${run.repository_id} / ${run.github_repo}`,
    `模板 ${run.template_id} · 修订 v${run.template_revision}`,
    `版本 ${run.version} · 源 HEAD ${run.source_sha}${run.install_extras ? ' · 测试 extras ' + run.install_extras : ''}`,
    `创建 ${run.created_at || '未知'} · 更新 ${run.updated_at || '未知'}`,
    run.cleanup ? `Sandbox 清理核验 ${run.cleanup.container_absent ? '容器已不存在' : '未确认'}${run.sandbox_id ? ' · ' + run.sandbox_id : ''}` : 'Sandbox 清理状态：尚无结果',
    run.release ? `标签 ${run.release.tag || '未创建'} · ${run.release.publication || '等待验证'}` : '',
    run.error ? `错误 ${run.error}` : ''
  ].filter(Boolean);
  summary.textContent = lines.join('\n');
  summary.style.whiteSpace = 'pre-wrap';
  const stepList = $('run-steps'); stepList.replaceChildren();
  for (const step of run.steps || []) {
    const item = document.createElement('li');
    const heading = document.createElement('div'); heading.className = 'step-heading';
    const name = document.createElement('span'); name.textContent = step.kind + (step.suite ? ` · ${step.suite}` : '');
    const state = document.createElement('span'); state.textContent = step.status;
    heading.append(name, state); item.append(heading);
    const time = document.createElement('div'); time.className = 'step-time';
    time.textContent = [step.started_at, step.ended_at].filter(Boolean).join(' → '); item.append(time);
    if (step.receipt || step.error) {
      const output = document.createElement('pre');
      output.textContent = step.error || JSON.stringify(step.receipt, null, 2);
      item.append(output);
    }
    stepList.append(item);
  }
  const artifactList = $('artifact-list'); artifactList.replaceChildren();
  for (const artifact of run.artifacts || []) {
    const item = document.createElement('div');
    const link = document.createElement('a');
    link.href = `/api/runs/${encodeURIComponent(run.id)}/artifacts/${encodeURIComponent(artifact.name)}`;
    link.textContent = `下载 ${artifact.name} · ${artifact.size} bytes`;
    const digest = document.createElement('code'); digest.textContent = `SHA256 ${artifact.sha256}`;
    item.append(link, digest); artifactList.append(item);
  }
  $('cancel-run').hidden = !['queued', 'running'].includes(run.status);
  $('approval-box').hidden = run.status !== 'awaiting_approval';
  if (run.status === 'awaiting_approval') {
    $('approval-target').textContent = approvalPhrase(run);
    if (!run.pull_request) show('run-error', '该运行未绑定 PR 编号。要发布，请带已合并 PR 编号创建新预检。', true);
    else show('run-error', '');
  } else show('run-error', run.error || '', !!run.error);
  $('approval-confirmation').value = '';
  deployTemplateControls();
}
$('refresh-run').addEventListener('click', async () => {
  try {
    await refreshHistory();
    if (selectedRun) await loadRun(selectedRun.id);
  } catch (error) { show('run-error', `刷新失败：${error.message}。现有内容可能已过期。`, true); }
});
$('create-run').addEventListener('click', async () => {
  if (templateBusy) return;
  let body = pendingCreate;
  if (!body) {
    const version = $('target-version').value.trim();
    const pr = $('pull-request').value.trim();
    const extras = $('install-extras').value.trim();
    if (!version) return show('create-state', '请输入目标版本。', true);
    if (extras && (!/^[A-Za-z0-9][A-Za-z0-9_-]*(,[A-Za-z0-9][A-Za-z0-9_-]*)*$/.test(extras) || extras.length > 80 || extras.split(',').length > 4))
      return show('create-state', '依赖 extras 最多四个，只能使用字母、数字、下划线、短横线和逗号。', true);
    if (pr && (!/^\d+$/.test(pr) || Number(pr) < 1 || !Number.isSafeInteger(Number(pr))))
      return show('create-state', 'PR 编号必须是正整数。', true);
    body = {request_id: crypto.randomUUID(), repository_id: $('repository-picker').value,
            template_id: $('template-picker').value, version};
    if (pr) body.pull_request = Number(pr);
    if (extras) body.install_extras = extras;
    pendingCreate = body;
    sessionStorage.setItem('chattask-pending-run', JSON.stringify(body));
  }
  templateBusy = true; deployTemplateControls();
  show('create-state', '正在检查已登记仓库并创建预检…');
  try {
    const run = await request('/api/runs', 'POST', body);
    pendingCreate = null; sessionStorage.removeItem('chattask-pending-run');
    await refreshHistory(); await loadRun(run.id);
    show('create-state', `运行已创建 · ${run.id.slice(0, 8)}`);
  } catch (error) {
    try {
      const runs = await refreshHistory();
      const found = runs.find(r => r.request_id === body.request_id);
      if (found) {
        pendingCreate = null; sessionStorage.removeItem('chattask-pending-run');
        await loadRun(found.id);
        show('create-state', '已通过同一请求 ID 找回创建的运行。');
      } else show('create-state', `${error.message}。结果未确认；再次点击会复用同一请求 ID。`, true);
    } catch (_) { show('create-state', `${error.message}。无法核对创建结果；再次点击会复用同一请求 ID。`, true); }
  } finally { templateBusy = false; deployTemplateControls(); }
});
$('cancel-run').addEventListener('click', async () => {
  if (!selectedRun || templateBusy) return;
  templateBusy = true; deployTemplateControls();
  try {
    const run = await request(`/api/runs/${selectedRun.id}/cancel`, 'POST');
    selectedRun = run; renderRun(run); show('run-error', '取消请求已记录。等当前命令结束并核验 Sandbox 清理后才会显示已取消。');
    await loadRun(run.id);
  } catch (error) { show('run-error', `取消结果未确认：${error.message}。请刷新同一运行核对。`, true); }
  finally { templateBusy = false; deployTemplateControls(); }
});
$('approve-run').addEventListener('click', async () => {
  const run = selectedRun;
  if (!run || templateBusy || $('approval-confirmation').value !== approvalPhrase(run)) return;
  templateBusy = true; deployTemplateControls();
  const body = {action: 'tag', repository_id: run.repository_id, version: run.version,
                source_sha: run.source_sha, confirmation: approvalPhrase(run)};
  try {
    const updated = await request(`/api/runs/${run.id}/approve`, 'POST', body);
    selectedRun = updated; renderRun(updated);
  } catch (error) {
    show('run-error', `批准结果未确认：${error.message}。请刷新同一运行核对远端标签。`, true);
    await loadRun(run.id);
  } finally { templateBusy = false; deployTemplateControls(); }
});
deployTemplateControls();
