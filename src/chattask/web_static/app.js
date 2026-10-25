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
  if (!response.ok) {
    const error = new Error(typeof payload.detail === 'string' ? payload.detail : `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
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
    await initializeAgentTasks();
    await initializePackages();
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
  clearAgentState();
  clearPackageState();
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
  $('agent-view').hidden = key !== 'agent';
  $('agent-tab').setAttribute('aria-selected', String(key === 'agent'));
  $('package-view').hidden = key !== 'package';
  $('package-tab').setAttribute('aria-selected', String(key === 'package'));
  $('template-view').hidden = key !== 'templates';
  $('sandbox-view').hidden = key !== 'sandbox';
  $('templates-tab').setAttribute('aria-selected', String(key === 'templates'));
  $('sandbox-tab').setAttribute('aria-selected', String(key === 'sandbox'));
}
$('agent-tab').addEventListener('click', () => selectTab('agent'));
$('package-tab').addEventListener('click', () => selectTab('package'));
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
    renderHistory(history.runs.filter(r => r.engine !== 'codex'));
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
    show('tool-state', connection.available ? `ChatPyPI 元数据工具已连接：${(connection.tools || []).join(', ')}。正式发版仍需 PR/CI 与单独批准；此连接没有 PyPI 上传权限。` :
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
    `版本 ${run.version} · 源 HEAD ${run.source_sha}${run.source_tree ? ' · 树 ' + run.source_tree : ''}${run.install_extras ? ' · 测试 extras ' + run.install_extras : ''}`,
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
    link.href = `/api/agent/tasks/${encodeURIComponent(run.id)}/artifacts/${encodeURIComponent(artifact.name)}`;
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
                source_sha: run.source_sha, source_tree: run.source_tree, confirmation: approvalPhrase(run)};
  try {
    const updated = await request(`/api/runs/${run.id}/approve`, 'POST', body);
    selectedRun = updated; renderRun(updated);
  } catch (error) {
    show('run-error', `批准结果未确认：${error.message}。请刷新同一运行核对远端标签。`, true);
    await loadRun(run.id);
  } finally { templateBusy = false; deployTemplateControls(); }
});
deployTemplateControls();

// Generic tasks: instruction + image profile -> Codex/Sandbox -> persisted result.
let agentSelected = null;
let agentPoll = null;
let agentGeneration = 0;
let agentPending = null;
function clearAgentState() {
  agentGeneration += 1;
  if (agentPoll) clearTimeout(agentPoll);
  agentPoll = null; agentSelected = null; agentPending = null;
  sessionStorage.removeItem('chattask-agent-pending');
  $('agent-instructions').value = '';
  $('agent-result').textContent = '';
  $('agent-artifacts').replaceChildren();
  $('agent-history').replaceChildren();
  $('agent-create').disabled = true;
}
async function initializeAgentTasks() {
  const generation = agentGeneration;
  try {
    const [images, history] = await Promise.all([request('/api/agent/images'), request('/api/agent/tasks')]);
    if (!authenticated || generation !== agentGeneration) return;
    $('agent-image').replaceChildren();
    images.images.forEach(image => $('agent-image').append(option(image.id, image.id)));
    $('agent-create').disabled = !images.images.length;
    show('agent-create-state', images.images.length ? '选择镜像并说明任务；无需指定仓库或版本。' : '执行器尚未配置：请管理员设置 Codex 网关与镜像配置。', !images.images.length);
    const pending = sessionStorage.getItem('chattask-agent-pending');
    if (pending) { try { agentPending = JSON.parse(pending); } catch (_) { sessionStorage.removeItem('chattask-agent-pending'); } }
    renderAgentHistory(history.tasks);
    if (history.tasks.length) await loadAgentTask(history.tasks[0].id);
  } catch (error) { show('agent-create-state', error.message, true); }
}
function renderAgentHistory(tasks) {
  const picker = $('agent-history'); picker.replaceChildren(option('', '选择任务'));
  [...tasks].sort((a,b) => b.created_at.localeCompare(a.created_at)).forEach(task =>
    picker.append(option(task.id, `${String(task.instructions || task.template_id || '既有任务').slice(0, 28)} · ${task.status}`)));
  if (agentSelected) picker.value = agentSelected.id;
}
async function loadAgentTask(id) {
  if (!id) return;
  const generation = agentGeneration;
  try {
    const task = await request(`/api/agent/tasks/${id}`);
    if (!authenticated || generation !== agentGeneration) return;
    agentSelected = task; $('agent-history').value = task.id;
    const states = {queued:'排队中', running:'Codex 正在 Sandbox 中执行', completed:'执行结束', blocked:'执行受阻', interrupted:'执行中断', cancelled:'已取消', cancel_requested:'等待安全取消'};
    show('agent-state', `${states[task.status] || task.status} · ${task.image_profile} · ${task.id.slice(0,8)}${task.cleanup?.container_absent ? ' · 环境已回收' : ''}`, task.status === 'blocked');
    $('agent-result').textContent = task.result || task.error || task.steps.map(s => `${s.kind}: ${s.status}`).join('\n');
    $('agent-artifacts').replaceChildren();
    (task.artifacts || []).forEach(item => {
      const link = document.createElement('a'); link.href = `/api/agent/tasks/${task.id}/artifacts/${encodeURIComponent(item.name)}`;
      link.textContent = `${item.name} · ${item.size} bytes`; link.download = item.name;
      $('agent-artifacts').append(link);
    });
    if (agentPoll) clearTimeout(agentPoll);
    if (['queued','running','cancel_requested'].includes(task.status)) agentPoll = setTimeout(() => loadAgentTask(task.id), 1800);
  } catch (error) { show('agent-state', `结果暂未确认：${error.message}`, true); }
}
$('agent-create').addEventListener('click', async () => {
  if (!authenticated) return;
  const instructions = $('agent-instructions').value.trim();
  if (!agentPending && !instructions) return show('agent-create-state', '请填写任务说明。', true);
  if (!agentPending && /发(?:一?个)?\s*(?:Python\s*)?包|(?:发布|新建|创建).*(?:Python|python|包)|(?:publish|release).*\b(?:python|package)\b/i.test(instructions)) {
    try { await beginPackageIntent(instructions); }
    catch (error) { show('agent-create-state', error.message, true); }
    return;
  }
  if (!agentPending) agentPending = {request_id: crypto.randomUUID(), instructions, image_profile: $('agent-image').value};
  sessionStorage.setItem('chattask-agent-pending', JSON.stringify(agentPending));
  $('agent-create').disabled = true; show('agent-create-state', '正在创建任务…');
  try {
    const task = await request('/api/agent/tasks', 'POST', agentPending);
    agentPending = null; sessionStorage.removeItem('chattask-agent-pending');
    show('agent-create-state', '任务已创建；关闭页面不会撤销任务。');
    const history = await request('/api/agent/tasks');
    if (!authenticated) return;
    renderAgentHistory(history.tasks); await loadAgentTask(task.id);
  } catch (error) { show('agent-create-state', `创建结果尚未确认：${error.message}。再次点击将核对同一请求。`, true); }
  finally { $('agent-create').disabled = !authenticated || !$('agent-image').value; }
});
$('agent-history').addEventListener('change', () => loadAgentTask($('agent-history').value));
$('agent-refresh').addEventListener('click', () => agentSelected ? loadAgentTask(agentSelected.id) : initializeAgentTasks());


// Versioned package form. No package permission or policy is accepted from browser input.
let packageSchema = null;
let packageDraft = null;
let packagePoll = null;
let packagePending = null;
let packageBusy = false;
let packageGeneration = 0;
let packageLoadSequence = 0;
const packagePendingKey = 'chattask-package-pending';
function clearPackageState() {
  packageGeneration++; packageLoadSequence++;
  if (packagePoll) clearTimeout(packagePoll);
  packagePoll = null; packageDraft = null; packageSchema = null; packageBusy = false;
  packagePendingSet(null);
  $('package-fields').replaceChildren(); $('package-history').replaceChildren();
  $('package-artifacts').replaceChildren(); $('package-image').replaceChildren();
  for (const id of ['package-link','package-progress','package-preview','package-state']) $(id).textContent = '';
  $('package-intent').value = ''; $('package-confirm-text').checked = false;
  $('package-confirm-box').hidden = true;
  for (const id of ['package-save','package-prepare','package-confirm','package-prefill','package-retry-confirm']) $(id).disabled = true;
}
function suggestPackageFields() {
  const value = $('pkg-package_name')?.value || '';
  if (!/^[A-Za-z][A-Za-z0-9._-]*$/.test(value)) return;
  const suggestions = {module:value.toLowerCase().replace(/[-.]+/g,'_'),cli:value.toLowerCase().replace(/[_.]+/g,'-'),repository:value};
  for (const [key, suggestion] of Object.entries(suggestions)) {
    const input = $('pkg-' + key);
    if (input && (!input.value || input.value === input.dataset.suggested)) { input.value = suggestion; input.dataset.suggested = suggestion; }
  }
}
async function beginPackageIntent(text) {
  if (text.length > 500) throw new Error('发包一句话最多 500 字；详细要求可在参数表单中填写。');
  if (packageBusy || packagePending || packageDraft && packageDirty()) throw new Error('请先保存或放弃当前发包草稿，再开始新需求。');
  if (!packageSchema) await initializePackages();
  if (!packageSchema) throw new Error('模板尚未加载，请重试。');
  const generation = packageGeneration;
  const value = await request('/api/package/intent','POST',{text});
  if (!authenticated || generation !== packageGeneration) return;
  await loadPackage(''); $('package-tab').click(); $('package-intent').value = text;
  fillPackage({...packageSchema.defaults,mode:'new',...value.prefill,description:text,requirements:text});
  suggestPackageFields(); $('package-confirm-box').hidden = true;
  $('package-view').scrollIntoView({block:'start',behavior:'smooth'});
  $('package-preview').textContent = '补充并确认参数后保存草稿，再运行发布前检查。';
  show('package-state','已识别发包需求。请核对参数；现在还不会创建仓库或发布。');
}
function packageFields() {
  const spec = {};
  if (!packageSchema) return spec;
  packageSchema.fields.forEach(field => { spec[field.name] = $('pkg-' + field.name).value.trim(); });
  return spec;
}
function packageDirty() {
  return !!packageDraft && JSON.stringify(packageFields()) !== JSON.stringify(packageDraft.spec);
}
function packageControls() {
  const hasDraft = !!packageDraft;
  const dirty = packageDirty();
  const submitted = hasDraft && !!packageDraft.publish_task_id;
  $('package-history').disabled = packageBusy || !!packagePending;
  $('package-save').disabled = !authenticated || packageBusy || submitted || !packageSchema;
  $('package-prepare').disabled = !authenticated || packageBusy || !hasDraft || dirty || submitted || !packageSchema?.available ||
    ['queued','running','cancel_requested'].includes(packageDraft.prepare_status);
  $('package-prefill').disabled = !authenticated || packageBusy;
  $('package-confirm').disabled = !authenticated || packageBusy || !hasDraft || dirty || !packageSchema?.available ||
    !packageDraft.preview || submitted || !$('package-confirm-text').checked;
  $('package-retry-confirm').disabled = !authenticated || packageBusy || !hasDraft ||
    packageDraft.publish_error !== 'GatewayUnknown';
}
function packagePendingSet(value) {
  packagePending = value;
  if (value) sessionStorage.setItem(packagePendingKey, JSON.stringify(value));
  else sessionStorage.removeItem(packagePendingKey);
}
async function packageMutation(kind, path, method, body) {
  const generation = packageGeneration;
  packageLoadSequence++;
  if (packagePoll) clearTimeout(packagePoll);
  if (packagePending && packagePending.kind !== kind) throw new Error('上一次请求结果未确认；请先重试同一操作。');
  const pending = packagePending || {kind, path, method, body};
  if (!packagePending) packagePendingSet(pending);
  try {
    const result = await request(pending.path, pending.method, pending.body);
    if (!authenticated || generation !== packageGeneration) throw new Error('身份已变化，请重新读取草稿。');
    packagePendingSet(null);
    return result;
  } catch (error) {
    // Authoritative input/permission rejection is not an uncertain mutation.
    if (error.status >= 400 && error.status < 500) packagePendingSet(null);
    throw error;
  }
}
function fillPackage(spec) {
  packageSchema.fields.forEach(field => { $('pkg-' + field.name).value = spec[field.name] || ''; });
  packageControls();
}
function renderPackage(draft) {
  if (!authenticated) return;
  if (!draft.preview || packageDraft?.preview?.preview_sha256 !== draft.preview.preview_sha256) $('package-confirm-text').checked = false;
  packageDraft = draft;
  $('package-history').value = draft.id;
  history.replaceState(null, '', '#package=' + draft.id);
  $('package-link').textContent = '草稿链接：' + location.href + ' · 修订 ' + draft.revision + ' · ' + draft.fingerprint;
  const dirty = packageDirty();
  const prep = draft.prepare_task_id ? '预检任务 ' + draft.prepare_task_id + ' · ' + (draft.prepare_status || '可刷新') : '尚未预检';
  const pub = draft.publish_task_id ? '；发布任务 ' + draft.publish_task_id + ' · ' + (draft.publish_status || '可刷新') : '';
  show('package-progress', prep + pub + (dirty ? '；参数有未保存修改' : ''));
  const preview = draft.preview;
  $('package-confirm-box').hidden = !preview || !!draft.publish_task_id || dirty;
  $('package-retry-confirm').hidden = draft.publish_error !== 'GatewayUnknown';
  $('package-confirm-target').textContent = preview ? preview.policy.repository + ' · ' + preview.policy.tag : '';
  if (preview) {
    $('package-preview').textContent = JSON.stringify({
      target: preview.policy.repository, project: preview.policy.project, version: preview.policy.version,
      actions: preview.policy.actions, revision: preview.revision, preparation_task: preview.task_id,
      plan_sha256: preview.plan_sha256, archive_sha256: preview.archive_sha256,
      source_manifests: preview.source_manifests, artifact_digests: preview.artifact_digests,
      validation: preview.validation
    }, null, 2);
  } else $('package-preview').textContent = draft.prepare_task_id ?
    '预检任务正在执行或结果尚未验证。刷新后查看精确目标与产物摘要。' : '请先保存草稿并运行预检。';
  $('package-artifacts').replaceChildren();
  for (const id of [draft.prepare_task_id, draft.publish_task_id]) {
    if (!id) continue;
    const a = document.createElement('a');
    a.href = '/api/agent/tasks/' + id;
    a.textContent = (id === draft.prepare_task_id ? '预检' : '发布') + '任务状态 · ' + id;
    a.target = '_blank'; a.rel = 'noopener';
    $('package-artifacts').append(a);
    const download = document.createElement('a');
    download.href = '/api/agent/tasks/' + id + '/artifacts/result.tar.gz';
    download.textContent = '下载任务产物 · ' + id.slice(0, 8);
    $('package-artifacts').append(download);
  }
  if (draft.prepare_error) show('package-state', '预检验证受阻：' + draft.prepare_error, true);
  if (draft.publish_result) show('package-state', '任务结果：' + draft.publish_result);
  else if (draft.publish_error) show('package-state', '发布任务受阻：' + draft.publish_error, true);
  packageControls();
  if (packagePoll) clearTimeout(packagePoll);
  if (draft.prepare_task_id && !preview && ['queued','running','cancel_requested'].includes(draft.prepare_status))
    packagePoll = setTimeout(() => loadPackage(draft.id), 2500);
  if (draft.publish_task_id && ['queued','running','cancel_requested'].includes(draft.publish_status))
    packagePoll = setTimeout(() => loadPackage(draft.id), 2500);
}
async function loadPackage(id) {
  if (!id) {
    packageLoadSequence++; packageDraft = null;
    if (packagePoll) clearTimeout(packagePoll);
    history.replaceState(null, '', location.pathname); $('package-link').textContent = '';
    fillPackage({...packageSchema.defaults,mode:'new'}); $('package-preview').textContent = '请先保存草稿并运行预检。';
    $('package-progress').textContent = ''; $('package-artifacts').replaceChildren();
    $('package-confirm-text').checked = false; $('package-confirm-box').hidden = true;
    packageControls(); return;
  }
  const generation = packageGeneration; const sequence = ++packageLoadSequence;
  try {
    const draft = await request('/api/package/drafts/' + id);
    if (!authenticated || generation !== packageGeneration || sequence !== packageLoadSequence) return;
    if (!(packageDraft?.id === id && packageDirty())) fillPackage(draft.spec);
    renderPackage(draft);
  } catch (error) { show('package-state', '读取草稿失败：' + error.message, true); }
}
async function packageHistory() {
  const generation = packageGeneration;
  const data = await request('/api/package/drafts');
  if (!authenticated || generation !== packageGeneration) return;
  $('package-history').replaceChildren(option('', '新草稿'));
  data.drafts.forEach(draft => $('package-history').append(option(draft.id, draft.spec.package_name + ' · 修订 ' + draft.revision)));
  if (packageDraft) $('package-history').value = packageDraft.id;
}
async function initializePackages() {
  const generation = packageGeneration;
  try {
    packageSchema = await request('/api/package/schema');
    if (!authenticated || generation !== packageGeneration) { packageSchema = null; return; }
    const fields = $('package-fields'); fields.replaceChildren();
    packageSchema.fields.forEach(field => {
      const label = document.createElement('label'); label.htmlFor = 'pkg-' + field.name; label.textContent = field.label;
      fields.append(label);
      let input;
      if (field.type === 'choice') {
        input = document.createElement('select');
        const labels = {new:'新建包（含仓库与 Publisher）',existing:'已有项目发新版',bilingual_mkdocs:'中英文 MkDocs',none:'不生成文档'};
        field.choices.forEach(choice => input.append(option(choice, labels[choice] || choice)));
      } else input = document.createElement(field.type === 'textarea' ? 'textarea' : 'input');
      input.id = 'pkg-' + field.name; input.value = field.default || '';
      input.addEventListener('input', () => { if (field.name === 'package_name') suggestPackageFields(); packageControls(); });
      fields.append(input);
    });
    $('package-image').replaceChildren();
    const images = await request('/api/agent/images');
    if (!authenticated || generation !== packageGeneration) return;
    images.images.forEach(image => $('package-image').append(option(image.id, image.id)));
    await packageHistory();
    try { packagePending = JSON.parse(sessionStorage.getItem(packagePendingKey) || 'null'); }
    catch (_) { packagePendingSet(null); }
    const match = /^#package=([a-f0-9]{32})$/.exec(location.hash);
    if (match) { await loadPackage(match[1]); $('package-tab').click(); }
    else fillPackage({...packageSchema.defaults,mode:'new'});
    show('package-state', packagePending ? '上次操作结果未确认；重复相同按钮将使用原请求 ID。' : packageSchema.available ? '填写参数后保存草稿。' : '发包网关尚未配置；可填写草稿，预检与发布暂不可用。', !packageSchema.available);
    packageControls();
  } catch (error) { show('package-state', error.message, true); }
}
$('package-prefill').addEventListener('click', async () => {
  try {
    const generation = packageGeneration;
    const value = await request('/api/package/intent', 'POST', {text: $('package-intent').value});
    if (!authenticated || generation !== packageGeneration) return;
    Object.entries(value.prefill).forEach(([key,val]) => { if ($('pkg-' + key)) $('pkg-' + key).value = val; });
    suggestPackageFields();
    show('package-state', '已填入明显的名称/模式。请补充模块、功能要求和目标。');
    packageControls();
  } catch (error) { show('package-state', error.message, true); }
});
$('package-save').addEventListener('click', async () => {
  if (packageBusy) return;
  packageBusy = true; packageControls();
  try {
    const spec = packageFields();
    const editing = !!packageDraft;
    const body = editing ? {request_id: crypto.randomUUID(),revision:packageDraft.revision,spec} :
      {request_id:crypto.randomUUID(),spec};
    const draft = await packageMutation(editing ? 'update' : 'create',
      editing ? '/api/package/drafts/' + packageDraft.id : '/api/package/drafts',
      editing ? 'PUT' : 'POST', body);
    await packageHistory(); fillPackage(draft.spec); renderPackage(draft);
    show('package-state', '草稿已保存；参数变更会使先前预检和确认失效。');
  } catch (error) { show('package-state', '保存结果未确认：' + error.message + '。重试会使用同一请求 ID。', true); }
  finally { packageBusy = false; packageControls(); }
});
$('package-prepare').addEventListener('click', async () => {
  if (!packageDraft || packageDirty() || packageBusy) return;
  packageBusy = true; packageControls();
  try {
    const requestId = packageDraft.prepare_error === 'GatewayUnknown' ? packageDraft.prepare_request_id : crypto.randomUUID();
    const body = {request_id:requestId,revision:packageDraft.revision,image_profile:requestId === packageDraft.prepare_request_id ? packageDraft.prepare_image_profile : $('package-image').value};
    const draft = await packageMutation('prepare','/api/package/drafts/' + packageDraft.id + '/prepare','POST',body);
    renderPackage(draft); show('package-state', '真实预检已提交；请刷新读取验证后的产物。');
  } catch (error) { show('package-state', '预检提交结果未确认：' + error.message + '。重试会使用同一请求 ID。', true); }
  finally { packageBusy = false; packageControls(); }
});
$('package-confirm').addEventListener('click', async () => {
  if (!packageDraft || !packageDraft.preview || packageDirty() || packageBusy) return;
  packageBusy = true; packageControls();
  try {
    const body = {request_id:crypto.randomUUID(),revision:packageDraft.revision,
      preview_sha256:packageDraft.preview.preview_sha256,confirmation:packageDraft.preview.confirmation};
    const draft = await packageMutation('confirm','/api/package/drafts/' + packageDraft.id + '/confirm','POST',body);
    renderPackage(draft); show('package-state', '已提交精确发布任务；完成状态以任务和注册表回读为准。');
  } catch (error) { show('package-state', '确认结果未确认：' + error.message + '。重试会使用同一请求 ID。', true); }
  finally { packageBusy = false; packageControls(); }
});
$('package-confirm-text').addEventListener('input', packageControls);
$('package-history').addEventListener('change', () => loadPackage($('package-history').value));
$('package-refresh').addEventListener('click', async () => {
  if (packageDraft) await loadPackage(packageDraft.id);
  else await packageHistory();
});

$('package-retry-confirm').addEventListener('click', async () => {
  if (!packageDraft || packageDraft.publish_error !== 'GatewayUnknown' || packageBusy) return;
  packageBusy = true; packageControls();
  try {
    const body = {request_id:packageDraft.confirm_request_id,revision:packageDraft.revision,
      preview_sha256:packageDraft.preview.preview_sha256,confirmation:packageDraft.preview.confirmation};
    const draft = await packageMutation('confirm','/api/package/drafts/' + packageDraft.id + '/confirm','POST',body);
    renderPackage(draft);
    show('package-state', '已按同一任务 ID 重新核对网关会话。');
  } catch (error) { show('package-state', '同一任务重试未确认：' + error.message, true); }
  finally { packageBusy = false; packageControls(); }
});
