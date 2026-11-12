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
    await initializeDurableTasks();
    await initializePackages();
    const workspaceId = new URLSearchParams(location.search).get('workspace_task');
    if (workspaceId && /^[0-9a-f]{32}$/.test(workspaceId)) {
      selectTab('workspace');
      await initializeWorkspace(workspaceId);
    }
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
  clearWorkspaceState();
  clearAgentState();
  clearPackageState();
  clearDurableState();
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
  $('durable-view').hidden = key !== 'durable';
  $('durable-tab').setAttribute('aria-selected', String(key === 'durable'));
  $('package-view').hidden = key !== 'package';
  $('package-tab').setAttribute('aria-selected', String(key === 'package'));
  $('template-view').hidden = key !== 'templates';
  $('sandbox-view').hidden = key !== 'sandbox';
  $('templates-tab').setAttribute('aria-selected', String(key === 'templates'));
  $('sandbox-tab').setAttribute('aria-selected', String(key === 'sandbox'));
  $('workspace-view').hidden = key !== 'workspace';
  $('workspace-tab').setAttribute('aria-selected', String(key === 'workspace'));
}
$('agent-tab').addEventListener('click', () => selectTab('agent'));
$('durable-tab').addEventListener('click', () => selectTab('durable'));
$('package-tab').addEventListener('click', () => selectTab('package'));
$('templates-tab').addEventListener('click', () => selectTab('templates'));
$('sandbox-tab').addEventListener('click', () => selectTab('sandbox'));

// Long-term Tasks are queued here; execution belongs to the independent worker.
let durableSelected = null;
let durableCreateRequest = null;
let durableResumeRequest = null;
let durablePackage = null;
let durableLibrary = [];
let durableImportFiles = null;
let durableImportSequence = 0;
let durablePackageDirty = false;
let durableSelectionSequence = 0;
let durableLatestRun = null;
let taskLibrarySelectedRef = '';
let pendingPythonTemplate = false;
const durablePackageRequests = new Map();
function clearDurableState() {
  durableSelectionSequence += 1;
  durableImportSequence += 1; durablePackageDirty = false;
  durablePackage = null; durableLatestRun = null; durableLibrary = []; durableImportFiles = null;
  durablePackageRequests.clear(); clearPackageRendering();
  taskLibrarySelectedRef = ''; pendingPythonTemplate = false;
  $('task-template-picker').value = '';
  $('task-template-fields').hidden = true;
  $('task-template-title').value = ''; $('task-template-sources').value = '';
  $('task-resource-summary').textContent = '资源在服务端解析，凭据不进入任务定义。';
  $('durable-package-files').value = '';
  $('durable-package-import-state').textContent = '';
  $('durable-library-search').value = '';
  durableSelected = null; durableCreateRequest = null; durableResumeRequest = null;
  $('durable-history').replaceChildren(option('', '选择任务'));
  $('durable-runs').replaceChildren();
  $('durable-checkpoint').textContent = '';
  $('durable-project').replaceChildren();
  $('durable-worker-state').textContent = '';
  $('durable-state').textContent = '';
  $('durable-followup').value = '';
  $('durable-ack').checked = false;
}
async function initializeDurableTasks() {
  try {
    const [images, history, queue] = await Promise.all([request('/api/agent/images'), request('/api/task-library'), request('/api/durable/worker-state')]);
    renderDurableWorkerState(queue);
    $('durable-image').replaceChildren(...images.images.map(i => option(i.id, i.id)));
    $('durable-create').disabled = !images.images.length;
    if (!images.images.length) show('durable-create-state', '未配置可用镜像或模型网关。', true);
    renderDurableHistory(history.tasks);
    await initializeTaskTemplates();
    const requestedTask = new URLSearchParams(location.search).get('task');
    if (requestedTask && /^[a-f0-9]{32}$/.test(requestedTask)) { await loadDurable(requestedTask); selectTab('durable'); return; }
    const first = history.tasks.find(task => task.kind !== 'python-package');
    if (first) await loadDurable(first.id);
  } catch (e) { show('durable-create-state', e.message, true); }
}
function renderDurableWorkerState(queue) {
  $('durable-worker-state').textContent = !queue.configured ? '长期任务执行配置不可用。' :
    queue.reconciliation_required ? `队列已停在安全核对点：${queue.reconciliation_required} 个旧轮次仍需确认容器与网关租约。` :
    `待处理轮次 ${queue.pending}；此视图不表示独立 worker 已在线。`;
}
function renderDurableHistory(tasks) {
  durableLibrary = tasks;
  const picker = $('durable-history');
  const old = taskLibrarySelectedRef || (durableSelected && durableSelected.id);
  const query = $('durable-library-search').value.trim().toLocaleLowerCase();
  picker.replaceChildren(option('', '选择任务'));
  const reference = task => task.kind === 'python-package' ? 'python-package:' + task.id : task.id;
  tasks.filter(t => reference(t) === old || !query || `${t.goal} ${t.status} ${t.review?.decision || ''}`.toLocaleLowerCase().includes(query))
    .forEach(t => picker.append(option(reference(t), `${t.kind === 'python-package' ? 'Python 发版 · ' : ''}${t.goal.slice(0, 45)} · ${t.status}`)));
  if (old && tasks.some(t => reference(t) === old)) picker.value = old;
}
async function loadDurable(id) {
  if (durablePackageDirty && !window.confirm('规范有未保存的修改。是否放弃这些修改并读取所选任务？')) {
    $('durable-history').value = durableSelected?.id || ''; return;
  }
  durablePackageDirty = false;
  const sequence = ++durableSelectionSequence;
  durablePackage = null; durableLatestRun = null; clearPackageRendering();
  if (!id) { durableSelected = null; show('durable-state', '选择任务查看。'); return; }
  try {
    const [task, history] = await Promise.all([request(`/api/durable/tasks/${id}`), request(`/api/durable/tasks/${id}/runs`)]);
    if (sequence !== durableSelectionSequence || !authenticated) return;
    durableSelected = task;
    taskLibrarySelectedRef = id;
    $('durable-history').value = id;
    const status = task.status === 'queued' ? '已排队 · 等待独立 worker' :
      task.status === 'running' && task.pause_requested ? '运行中 · 已请求本轮结束后暂停' :
      task.status === 'waiting_input' ? '本轮完成 · 等待追加要求' :
      task.status === 'blocked' ? '已阻塞 · 检查失败或未知副作用后手动处理' : task.status;
    show('durable-state', `${status} · ${task.goal}`);
    $('durable-checkpoint').textContent = task.checkpoint ?
      `已验证检查点 · SHA256 ${task.checkpoint.sha256} · ${task.checkpoint.size} 字节` : '尚无已验证检查点';
    const project = $('durable-project'); project.replaceChildren();
    if (task.workflow === 'research' && task.research_project_url) {
      const link = document.createElement('a');
      link.href = task.research_project_url; link.textContent = '打开原 Overleaf 工程';
      link.target = '_blank'; link.rel = 'noopener noreferrer'; project.append(link);
    }
    const list = $('durable-runs'); list.replaceChildren();
    history.runs.forEach(run => {
      const item = document.createElement('li');
      const summary = document.createElement('div');
      summary.textContent = `第 ${run.number} 轮 · ${run.status} · ${run.actor} · ${run.instructions}`;
      item.append(summary);
      if (run.result || run.error) {
        const note = document.createElement('pre'); note.textContent = run.result || run.error; item.append(note);
      }
      if (run.unknown_side_effects) {
        const warning = document.createElement('p'); warning.textContent = '执行器中断；外部副作用未知，检查后才能明确确认接续。'; item.append(warning);
      }
      (run.artifacts || []).forEach(artifact => {
        const link = document.createElement('a');
        link.href = `/api/durable/tasks/${id}/runs/${run.id}/artifacts/${encodeURIComponent(artifact.name)}`;
        link.textContent = `${artifact.name} · ${artifact.size} 字节`;
        item.append(link);
      });
      const verification = document.createElement('p');
      verification.className = 'meta-note';
      verification.textContent = `独立验证：${run.evaluation?.status || '未执行'} · 人工验收：${run.review?.decision || '未确认'}`;
      item.append(verification);
      if (run.data_archive?.status === 'ready') {
        const dataLink = document.createElement('a');
        dataLink.href = `/api/durable/tasks/${id}/runs/${run.id}/data.zip`;
        dataLink.textContent = '下载本轮任务单元（定义、交付与评测证据）';
        item.append(dataLink);
      }
      list.append(item);
    });
    const latest = history.runs.at(-1);
    durableLatestRun = latest || null;
    $('durable-reconcile').hidden = !(task.workflow === 'research' && latest && latest.research_outcome_unknown);
    $('durable-resume').disabled = ['queued', 'running', 'reconciling'].includes(task.status) || !!(latest && latest.research_outcome_unknown);
    $('durable-pause').disabled = !['queued', 'running'].includes(task.status);
    $('durable-complete').disabled = ['queued', 'running', 'reconciling'].includes(task.status);
    try {
      const definition = await request(`/api/durable/tasks/${id}/package`);
      if (sequence !== durableSelectionSequence || durableSelected?.id !== id || !authenticated) return;
      durablePackage = definition;
      renderTaskPackage(task, definition, latest);
    } catch (e) {
      if (sequence === durableSelectionSequence) show('durable-package-state', `规范读取失败：${e.message}。不会显示其他任务的定义。`, true);
    }
  } catch (e) { if (sequence === durableSelectionSequence) show('durable-state', e.message, true); }
}
$('durable-history').addEventListener('change', e => openTaskLibraryEntry(e.target.value));
$('durable-refresh').addEventListener('click', async () => {
  try { const [history, queue] = await Promise.all([request('/api/task-library'), request('/api/durable/worker-state')]); renderDurableHistory(history.tasks); renderDurableWorkerState(queue); if (durableSelected) await loadDurable(durableSelected.id); }
  catch (e) { show('durable-state', e.message, true); }
});
$('durable-goal').addEventListener('input', () => { durableCreateRequest = null; });
$('durable-workflow').addEventListener('change', () => {
  durableCreateRequest = null;
  $('durable-sources-box').hidden = $('durable-workflow').value !== 'research';
});
$('durable-sources').addEventListener('input', () => { durableCreateRequest = null; durableResumeRequest = null; });
$('durable-auto-source').addEventListener('change', () => {
  durableCreateRequest = null;
  $('durable-auto-box').hidden = !$('durable-auto-source').checked;
  $('durable-manual-box').hidden = $('durable-auto-source').checked;
});
$('durable-query').addEventListener('input', () => { durableCreateRequest = null; });
$('durable-followup').addEventListener('input', () => { durableResumeRequest = null; });
$('durable-create').addEventListener('click', async () => {
  const instructions = $('durable-goal').value.trim();
  if (!instructions) return show('durable-create-state', '请输入目标。', true);
  durableCreateRequest ||= crypto.randomUUID();
  try {
    const template = $('task-template-picker').value;
    if (template === 'python-package') { selectTab('package'); throw new Error('请在 Python 发版参数编辑器保存 Task，不能用自由任务绕过发布授权。'); }
    if (template === 'research-report') {
      if (durableImportFiles) throw new Error('固定模板不能同时导入另一套验收文件。');
      const parameters = {title: $('task-template-title').value.trim(), requirements: instructions,
        language: $('task-template-language').value, output_name: $('task-template-output').value,
        sources: JSON.parse($('task-template-sources').value || '[]')};
      const task = await request('/api/task-templates/research-report/instantiate', 'POST',
        {request_id: durableCreateRequest, template_revision: 1, image_profile: $('durable-image').value, parameters});
      durableCreateRequest = null;
      const history = await request('/api/task-library'); renderDurableHistory(history.tasks); await loadDurable(task.id);
      show('durable-create-state', '已按固定模板创建独立 Task，参数、来源和验收代码已冻结。'); return;
    }
    const workflow = $('durable-workflow').value;
    const body = {request_id: durableCreateRequest, instructions, image_profile: $('durable-image').value, workflow};
    if (durableImportFiles) body.definition = durableImportFiles;
    const auto = workflow === 'research' && $('durable-auto-source').checked;
    if (workflow === 'research' && !auto) body.source_records = JSON.parse($('durable-sources').value);
    if (auto) {
      if (durableImportFiles) throw new Error('自动发现来源与导入任务包请分开使用；可先创建规范任务后补充来源。');
      body.query = $('durable-query').value.trim(); body.max_results = 3; delete body.workflow;
    }
    if (new TextEncoder().encode(JSON.stringify(body)).length > 65536) throw new Error('任务请求超过 Web 的 64 KiB 限制，请精简材料或使用 CLI。');
    const task = await request(auto ? '/api/durable/research/auto' : '/api/durable/tasks', 'POST', body);
    durableCreateRequest = null;
    show('durable-create-state', auto ? '摘要来源已自动发现并保存；请核对相关性。研究任务已排队。' : '任务已排队，等待独立 worker。');
    const history = await request('/api/task-library'); renderDurableHistory(history.tasks); await loadDurable(task.id);
  } catch (e) { show('durable-create-state', e.message, true); }
});
$('durable-resume').addEventListener('click', async () => {
  if (!durableSelected) return;
  const instructions = $('durable-followup').value.trim();
  if (!instructions) return show('durable-state', '请输入本轮追加要求。', true);
  durableResumeRequest ||= crypto.randomUUID();
  try {
    const body = {
      request_id: durableResumeRequest, instructions, acknowledge_unknown: $('durable-ack').checked};
    if (durableSelected.workflow === 'research' && $('durable-sources').value.trim()) {
      body.source_records = JSON.parse($('durable-sources').value);
    }
    await request(`/api/durable/tasks/${durableSelected.id}/resume`, 'POST', body);
    durableResumeRequest = null; $('durable-ack').checked = false;
    await loadDurable(durableSelected.id);
  } catch (e) { show('durable-state', e.message, true); }
});
for (const action of ['pause', 'complete']) {
  $(`durable-${action}`).addEventListener('click', async () => {
    if (!durableSelected) return;
    try {
      await request(`/api/durable/tasks/${durableSelected.id}/${action}`, 'POST', {request_id: crypto.randomUUID()});
      await loadDurable(durableSelected.id);
    } catch (e) { show('durable-state', e.message, true); }
  });
}
$('durable-reconcile').addEventListener('click', async () => {
  if (!durableSelected) return;
  if (!$('durable-ack').checked) return show('durable-state', '请先检查原工程并勾选未知副作用确认。', true);
  try {
    await request(`/api/durable/tasks/${durableSelected.id}/reconcile-research`, 'POST',
      {request_id: crypto.randomUUID(), acknowledge_unknown: true});
    await loadDurable(durableSelected.id);
  } catch (e) { show('durable-state', e.message, true); }
});

async function initializeTaskTemplates() {
  try {
    const [catalog, resources] = await Promise.all([request('/api/task-templates'), request('/api/task-resources')]);
    if (!authenticated) return;
    const picker = $('task-template-picker');
    picker.replaceChildren(option('', '自由任务（初始化后完善规范）'), ...catalog.templates.map(template => option(template.id, `${template.name} · v${template.revision}`)));
    picker.disabled = false;
    $('task-template-state').textContent = '模板固定方法、环境约束和验收逻辑；每次填写参数生成独立 Task。';
    $('task-resource-summary').textContent = `模型入口：${resources.model.configured ? '已配置，尚未实测可用性' : '未配置'}；镜像配置：${resources.image_profiles.map(image => image.id).join('、') || '无'}；工具网关：${resources.gateway.configured ? '已配置，按阶段授予' : '未配置'}。Publisher 复用既有 OIDC 链；资源声明不等于发布许可。`;
  } catch (error) { show('task-template-state', `模板目录暂不可用：${error.message}；自由任务仍可按已有配置创建。`, true); }
}
async function openTaskLibraryEntry(reference) {
  if (!reference.startsWith('python-package:')) return loadDurable(reference);
  const id = reference.slice('python-package:'.length);
  if (!/^[a-f0-9]{32}$/.test(id)) return;
  if (durablePackageDirty && !window.confirm('规范有未保存修改。是否放弃并查看 Python 发版任务？')) return;
  if (packageDraft && packageDraft.id !== id && packageDirty() && !window.confirm('发包参数有未保存修改。是否放弃并查看所选任务？')) return;
  durablePackageDirty = false; durableSelectionSequence += 1;
  durableSelected = null; durablePackage = null; clearPackageRendering();
  taskLibrarySelectedRef = reference;
  selectTab('package');
  if (!packageSchema) { show('package-state', '参数编辑器正在载入，请载入后从任务库重新选择。'); return; }
  await loadPackage(id);
}
$('task-template-picker').addEventListener('change', async event => {
  durableCreateRequest = null;
  const selected = event.target.value;
  $('task-template-fields').hidden = selected !== 'research-report';
  $('durable-workflow').disabled = !!selected;
  $('durable-package-files').disabled = !!selected;
  if (selected) { durableImportSequence += 1; durableImportFiles = null; $('durable-package-files').value = ''; }
  $('durable-create').textContent = selected === 'research-report' ? '按固定模板创建并排队' : '创建并排队';
  $('durable-create').disabled = selected === 'python-package' || !authenticated || !$('durable-image').value;
  if (selected === 'research-report') {
    $('durable-workflow').value = 'development'; $('durable-sources-box').hidden = true;
    $('task-template-state').textContent = '固定报告方法和验证代码；变化的是题目、要求、来源与语言。没有来源时不得声称读过文献。';
  } else if (selected === 'python-package') {
    selectTab('package');
    if (!packageSchema) { pendingPythonTemplate = true; show('package-state', '正在载入 Python 发版 Task 参数表单；保存不会自动发布。'); return; }
    if (packageDraft && packageDirty() && !window.confirm('发包参数尚未保存。是否放弃并创建新的发版 Task？')) return;
    await loadPackage(''); fillPackage({...packageSchema.defaults, mode: 'existing'});
    show('package-state', '填写目标和功能要求后保存为独立 Task。预检只读，发布必须另外确认精确计划。');
  }
});
for (const name of ['title', 'language', 'output', 'sources']) $('task-template-' + name).addEventListener('input', () => { durableCreateRequest = null; });

function clearPythonTaskUnit() {
  $('python-task-purpose').textContent = '';
  for (const name of ['resources', 'acceptance', 'stages']) $('python-task-' + name).replaceChildren();
  $('python-task-export').hidden = true; $('python-task-export').removeAttribute('href');
  $('python-task-data').hidden = true; $('python-task-data').removeAttribute('href');
  $('python-task-state').textContent = '保存参数后创建发版 Task；不会自动创建仓库、标签或执行上传。';
}
function renderPythonTaskUnit(draft) {
  clearPythonTaskUnit();
  const definition = draft.task_package;
  if (!definition) { $('python-task-state').textContent = '旧格式发包记录保留；没有补造历史任务规范或评分。'; return; }
  $('python-task-state').textContent = `Task ${draft.id} · 模板 ${definition.template.id}@${definition.template.revision} · 规范 ${definition.revision.slice(0, 12)} · ${draft.publish_verified ? '机器发布核验通过；人工业务验收单独判断' : draft.preview ? '预检通过，等待精确确认' : '等待预检与资源核对'}`;
  $('python-task-purpose').textContent = definition.purpose;
  const kinds = {model:'模型', environment:'受控运行环境', repository:'仓库', publisher:'发布渠道', 'package-index':'包索引与产物'};
  (definition.resources || []).forEach(resource => {
    const item = document.createElement('li');
    item.textContent = `${kinds[resource.kind] || resource.kind}：${resource.ref} · ${resource.access} · ${resource.verification}`;
    $('python-task-resources').append(item);
  });
  (definition.acceptance || []).forEach(criterion => {
    const item = document.createElement('li'); item.textContent = `${criterion.id} · ${criterion.description} · ${criterion.method === 'human' ? '人工业务复核' : '可信回执与脚本检查'}`;
    $('python-task-acceptance').append(item);
  });
  (draft.task_stages || []).forEach(stage => {
    const item = document.createElement('li');
    const label = {prepare:'只读预检',publish:'已确认目标的发布',verify:'只读重新核验'}[stage.kind] || stage.kind;
    item.textContent = `${label} · 草稿修订 ${stage.draft_revision} · 规范 ${stage.definition_revision.slice(0, 12)} · ${stage.native_task_id || '任务关联待回读'}`;
    $('python-task-stages').append(item);
  });
  const link = $('python-task-export'); link.hidden = false;
  link.href = `/api/package/drafts/${draft.id}/task-package.zip?revision=${encodeURIComponent(definition.revision)}`;
  $('python-task-data').hidden = false; $('python-task-data').href = `/api/package/drafts/${draft.id}/task-data.zip`;
}

function clearPackageRendering() {
  $('durable-package-purpose').textContent = '';
  $('durable-package-deliverables').replaceChildren();
  $('durable-package-acceptance').replaceChildren();
  $('durable-package-toml').value = '';
  $('durable-package-instruction').value = '';
  $('durable-package-review-notes').value = '';
  $('durable-package-decision').value = 'changes_requested';
  for (const name of ['toml', 'instruction', 'save', 'cancel', 'reuse', 'review']) $('durable-package-' + name).disabled = true;
  $('durable-package-export').hidden = true;
  $('durable-package-export').removeAttribute('href');
  show('durable-package-state', '选择任务后显示对应的规范与版本。');
}
function renderTaskPackage(task, definition, latest) {
  const legacy = definition.legacy;
  const editable = !legacy && definition.permissions?.edit && !['running', 'reconciling'].includes(task.status);
  const reviewable = !legacy && definition.permissions?.review && latest &&
    latest.package_revision === definition.revision && ['completed', 'blocked', 'interrupted'].includes(latest.status);
  show('durable-package-state', legacy ? '旧格式任务 · 原记录保留；没有补造历史测试或评分。' :
    `规范版本 ${definition.revision.slice(0, 12)} · ${definition.preparation === 'pending' ? '初始化草稿，尚未完成 Agent 准备' : definition.preparation} · 独立验证 ${latest?.evaluation?.status || '未执行'} · 人工验收 ${latest?.review?.decision || '未确认'}`);
  $('durable-package-purpose').textContent = definition.purpose || task.goal;
  const deliverables = $('durable-package-deliverables');
  (definition.deliverables.length ? definition.deliverables : ['尚未明确交付清单，需补充。']).forEach(text => {
    const li = document.createElement('li'); li.textContent = text; deliverables.append(li);
  });
  const criteria = $('durable-package-acceptance');
  definition.acceptance.forEach(criterion => {
    const li = document.createElement('li'); const label = document.createElement('label');
    const check = document.createElement('input'); check.type = 'checkbox'; check.dataset.criterion = criterion.id;
    check.disabled = !reviewable;
    label.append(check, document.createTextNode(` ${criterion.id} · ${criterion.description} · ${criterion.method}${criterion.required !== false ? ' · 必需' : ' · 可选'}`));
    li.append(label); criteria.append(li);
  });
  $('durable-package-toml').value = definition.files['task.toml'] || '';
  $('durable-package-instruction').value = definition.files['instruction.md'] || '';
  for (const name of ['toml', 'instruction', 'save', 'cancel']) $('durable-package-' + name).disabled = !editable;
  $('durable-package-reuse').disabled = legacy;
  $('durable-package-review').disabled = !reviewable;
  const link = $('durable-package-export'); link.hidden = legacy;
  if (!legacy) link.href = `/api/durable/tasks/${task.id}/package.zip?revision=${encodeURIComponent(definition.revision)}`;
}
function packageRequest(action, payload) {
  const identity = JSON.stringify({task: durableSelected.id, revision: durablePackage.revision, payload});
  const previous = durablePackageRequests.get(action);
  if (previous?.identity === identity) return {...payload, request_id: previous.requestId};
  const requestId = crypto.randomUUID();
  durablePackageRequests.set(action, {identity, requestId});
  return {...payload, request_id: requestId};
}
$('durable-library-search').addEventListener('input', () => renderDurableHistory(durableLibrary));
$('durable-package-files').addEventListener('change', async event => {
  const sequence = ++durableImportSequence;
  durableImportFiles = null; durableCreateRequest = null;
  const chosen = Array.from(event.target.files || []);
  if (!chosen.length) return show('durable-package-import-state', '未选择任务包，将按目标初始化草稿。');
  $('durable-create').disabled = true;
  try {
    if (chosen.length > 64 || chosen.reduce((sum, file) => sum + file.size, 0) > 48 * 1024) throw new Error('文本任务包最多 64 个文件、合计 48 KiB。');
    const files = Object.create(null);
    for (const file of chosen) {
      const relative = file.webkitRelativePath || file.name;
      const name = relative.includes('/') ? relative.slice(relative.indexOf('/') + 1) : relative;
      const parts = name.split('/');
      if (parts.some(part => !part || part.startsWith('.') || part === '..') || /[\\:]/.test(name) ||
          !(['task.toml', 'instruction.md'].includes(name) || /^(tests|inputs|environment)\//.test(name)) ||
          /(^|\/)(auth\.json|checkpoint\.tar\.gz|credentials\.json)$/.test(name) || /\.(key|pem)$/.test(name)) throw new Error(`不支持的任务包文件：${name}`);
      if (Object.hasOwn(files, name)) throw new Error(`重复任务包文件：${name}`);
      files[name] = new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer());
    }
    if (!files['task.toml'] || !files['instruction.md']) throw new Error('目录必须包含 task.toml 和 instruction.md。');
    if (!authenticated || sequence !== durableImportSequence) return;
    durableImportFiles = files; $('durable-goal').value = files['instruction.md'];
    show('durable-package-import-state', `已读取 ${chosen.length} 个文本文件；将按导入规范创建，不会上传凭据或原生会话。`);
  } catch (error) { if (authenticated && sequence === durableImportSequence) show('durable-package-import-state', error.message, true); }
  finally { if (sequence === durableImportSequence) $('durable-create').disabled = !authenticated || !$('durable-image').value; }
});
for (const name of ['toml', 'instruction']) $('durable-package-' + name).addEventListener('input', () => { durablePackageDirty = true; });
$('durable-package-cancel').addEventListener('click', () => {
  durablePackageDirty = false;
  if (durableSelected) loadDurable(durableSelected.id);
});
$('durable-package-save').addEventListener('click', async () => {
  if (!durableSelected || !durablePackage || durablePackage.legacy) return;
  const id = durableSelected.id; const sequence = durableSelectionSequence;
  const definition = {...durablePackage.files, 'task.toml': $('durable-package-toml').value,
    'instruction.md': $('durable-package-instruction').value};
  const body = packageRequest('save', {expected_revision: durablePackage.revision, definition});
  if (new TextEncoder().encode(JSON.stringify(body)).length > 65536) return show('durable-package-state', '规范超过 Web 64 KiB 请求限制，请使用 CLI。', true);
  $('durable-package-save').disabled = true;
  try {
    await request(`/api/durable/tasks/${id}/package`, 'PUT', body);
    if (durablePackageRequests.get('save')?.requestId === body.request_id) durablePackageRequests.delete('save');
    if (authenticated && sequence === durableSelectionSequence) {
      durablePackageDirty = false; await loadDurable(id);
    }
  } catch (error) {
    if (authenticated && sequence === durableSelectionSequence) {
      show('durable-package-state', `${error.message}；草稿已保留，重试使用同一请求 ID。`, true);
      $('durable-package-save').disabled = false;
    }
  }
});
$('durable-package-reuse').addEventListener('click', async () => {
  if (!durableSelected || !durablePackage || durablePackage.legacy) return;
  const id = durableSelected.id; const sequence = durableSelectionSequence;
  const body = packageRequest('reuse', {});
  $('durable-package-reuse').disabled = true;
  try {
    const created = await request(`/api/durable/tasks/${id}/reuse`, 'POST', body);
    if (durablePackageRequests.get('reuse')?.requestId === body.request_id) durablePackageRequests.delete('reuse');
    if (!authenticated || sequence !== durableSelectionSequence) return;
    const history = await request('/api/durable/tasks');
    if (!authenticated || sequence !== durableSelectionSequence) return;
    renderDurableHistory(history.tasks); await loadDurable(created.id);
  } catch (error) {
    if (authenticated && sequence === durableSelectionSequence) {
      show('durable-package-state', `${error.message}；结果未确认时不要换请求 ID 重复创建。`, true);
      $('durable-package-reuse').disabled = false;
    }
  }
});
$('durable-package-review').addEventListener('click', async () => {
  if (!durableSelected || !durablePackage || !durableLatestRun) return;
  const id = durableSelected.id; const sequence = durableSelectionSequence;
  const notes = $('durable-package-review-notes').value.trim();
  if (!notes) return show('durable-package-state', '请填写验收依据与意见。', true);
  const criteria = {};
  $('durable-package-acceptance').querySelectorAll('input[data-criterion]').forEach(input => { criteria[input.dataset.criterion] = input.checked; });
  const body = packageRequest('review', {expected_revision: durablePackage.revision, run_id: durableLatestRun.id,
    decision: $('durable-package-decision').value, notes, criteria});
  $('durable-package-review').disabled = true;
  try {
    await request(`/api/durable/tasks/${id}/review`, 'POST', body);
    if (durablePackageRequests.get('review')?.requestId === body.request_id) durablePackageRequests.delete('review');
    if (authenticated && sequence === durableSelectionSequence) await loadDurable(id);
  } catch (error) {
    if (authenticated && sequence === durableSelectionSequence) {
      show('durable-package-state', error.message, true); $('durable-package-review').disabled = false;
    }
  }
});

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
  clearPythonTaskUnit();
  packageGeneration++; packageLoadSequence++;
  if (packagePoll) clearTimeout(packagePoll);
  packagePoll = null; packageDraft = null; packageSchema = null; packageBusy = false;
  packagePendingSet(null);
  $('package-fields').replaceChildren(); $('package-history').replaceChildren();
  $('package-artifacts').replaceChildren(); $('package-image').replaceChildren();
  for (const id of ['package-link','package-progress','package-preview','package-state']) $(id).textContent = '';
  $('package-intent').value = ''; $('package-confirm-text').checked = false;
  $('package-confirm-box').hidden = true;
  for (const id of ['package-save','package-prepare','package-confirm','package-prefill','package-retry-confirm','package-recheck']) $(id).disabled = true;
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
  return !!packageDraft && Object.entries(packageFields()).some(([key,value]) => value !== packageDraft.spec[key]);
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
  $('package-recheck').disabled = !authenticated || packageBusy || !hasDraft || dirty || !packageSchema?.available ||
    !submitted || !['completed','blocked','interrupted','cancelled'].includes(packageDraft.publish_status) ||
    ['queued','running','cancel_requested'].includes(packageDraft.verification_status) ||
    !!packageDraft.publish_verified || !!(packagePending && packagePending.kind !== 'recheck');
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
  renderPythonTaskUnit(draft);
  $('package-history').value = draft.id;
  history.replaceState(null, '', '#package=' + draft.id);
  $('package-link').textContent = '草稿链接：' + location.href + ' · 修订 ' + draft.revision + ' · ' + draft.fingerprint;
  const dirty = packageDirty();
  const prep = draft.prepare_task_id ? '预检任务 ' + draft.prepare_task_id + ' · ' + (draft.prepare_status || '可刷新') : '尚未预检';
  const pub = draft.publish_task_id ? '；发布任务 ' + draft.publish_task_id + ' · ' + (draft.publish_status || '可刷新') : '';
  const verification = draft.verification_task_id ? '；只读核验任务 ' + draft.verification_task_id + ' · ' + (draft.verification_status || '可刷新') : '';
  show('package-progress', prep + pub + verification + (dirty ? '；参数有未保存修改' : ''));
  const preview = draft.preview;
  $('package-confirm-box').hidden = !preview || !!draft.publish_task_id || dirty;
  $('package-retry-confirm').hidden = draft.publish_error !== 'GatewayUnknown';
  $('package-recheck').hidden = !draft.publish_task_id || !['completed','blocked','interrupted','cancelled'].includes(draft.publish_status);
  $('package-recheck-note').hidden = $('package-recheck').hidden;
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
  for (const id of [draft.prepare_task_id, draft.publish_task_id, draft.verification_task_id]) {
    if (!id) continue;
    const a = document.createElement('a');
    a.href = '/api/agent/tasks/' + id;
    a.textContent = (id === draft.prepare_task_id ? '预检' : id === draft.publish_task_id ? '发布' : '只读核验') + '任务状态 · ' + id;
    a.target = '_blank'; a.rel = 'noopener';
    $('package-artifacts').append(a);
    const download = document.createElement('a');
    download.href = '/api/agent/tasks/' + id + '/artifacts/result.tar.gz';
    download.textContent = '下载任务产物 · ' + id.slice(0, 8);
    $('package-artifacts').append(download);
  }
  if (draft.prepare_error) show('package-state', '预检验证受阻：' + draft.prepare_error, true);
  if (draft.publish_task_id) {
    if (draft.verification_status === 'completed' && draft.publish_verified)
      show('package-state','重新核验通过；原执行记录保留。原发布任务：' + draft.publish_status + (draft.publish_error ? '（' + draft.publish_error + '）' : '') + '。');
    else if (draft.verification_status && ['blocked','interrupted','cancelled'].includes(draft.verification_status))
      show('package-state','只读重新核验受阻：' + (draft.verification_error || draft.verification_status) + '。原发布任务仍为 ' + draft.publish_status + '。',true);
    else if (draft.publish_error || ['blocked','interrupted','cancelled'].includes(draft.publish_status)) {
      show('package-state','发布核验未完成：' + (draft.publish_error || draft.publish_status) + '。模型摘要不能作为发布成功的依据。',true);
    } else if (draft.publish_verified) show('package-state','发布完成，外部回执和干净安装核验已通过。');
    else show('package-state','发布状态：' + draft.publish_status + '；仍需完整外部核验。');
    if (draft.publish_result) $('package-preview').textContent += '\n\n模型执行摘要（不代表发布核验结果）：\n' + draft.publish_result;
  }
  packageControls();
  if (packagePoll) clearTimeout(packagePoll);
  if (draft.prepare_task_id && !preview && ['queued','running','cancel_requested'].includes(draft.prepare_status))
    packagePoll = setTimeout(() => loadPackage(draft.id), 2500);
  if (draft.publish_task_id && ['queued','running','cancel_requested'].includes(draft.publish_status))
    packagePoll = setTimeout(() => loadPackage(draft.id), 2500);
  if (draft.verification_task_id && ['queued','running','cancel_requested'].includes(draft.verification_status))
    packagePoll = setTimeout(() => loadPackage(draft.id), 2500);
}
async function loadPackage(id) {
  if (!id) {
    packageLoadSequence++; packageDraft = null;
    clearPythonTaskUnit();
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
    else fillPackage({...packageSchema.defaults,mode:pendingPythonTemplate ? 'existing' : 'new'});
    pendingPythonTemplate = false;
    if (!packageDraft?.publish_task_id && !packageDraft?.prepare_error) show('package-state', packagePending ? '上次操作结果未确认；重复相同按钮将使用原请求 ID。' : !packageSchema.available ? '发包网关尚未配置；可填写草稿，预检与发布暂不可用。' : packageDraft ? '已读取草稿与预检状态。' : '填写参数后保存草稿。', !packageSchema.available);
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
    show('package-state', '发版 Task 草稿已保存；参数和资源声明已形成任务包。修改会使先前预检和确认失效。');
    const library = await request('/api/task-library'); renderDurableHistory(library.tasks);
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

$('package-recheck').addEventListener('click', async () => {
  if (!packageDraft || packageBusy || packageDirty()) return;
  packageBusy = true; packageControls();
  try {
    const body = {request_id:crypto.randomUUID(),revision:packageDraft.revision};
    const draft = await packageMutation('recheck','/api/package/drafts/' + packageDraft.id + '/recheck','POST',body);
    renderPackage(draft);
    show('package-state','只读核验已提交；不会再次发布、创建 PR 或标签。原执行记录保留。');
  } catch (error) {
    show('package-state','只读核验结果未确认：' + error.message + '。重试此按钮会使用同一请求 ID。',true);
  } finally { packageBusy = false; packageControls(); }
});

// Native Workspace material-analysis results: read-only, never start a Run here.
let workspaceSequence = 0;
let workspaceSelected = '';
let workspaceReportSequence = 0;
function clearWorkspaceState() {
  workspaceSequence++; workspaceReportSequence++; workspaceSelected = '';
  $('workspace-picker').replaceChildren(option('', '选择任务'));
  $('workspace-detail').textContent = '';
  $('workspace-runs').replaceChildren();
  $('workspace-report').textContent = '';
  $('workspace-state').textContent = '';
}
async function initializeWorkspace(preferredId = '') {
  const sequence = ++workspaceSequence;
  try {
    const data = await request('/api/workspace-tasks');
    if (!authenticated || sequence !== workspaceSequence) return;
    const picker = $('workspace-picker');
    picker.replaceChildren(option('', '选择任务'));
    data.tasks.forEach(task => picker.append(option(task.id, task.title)));
    if (preferredId && /^[0-9a-f]{32}$/.test(preferredId)) {
      if (!data.tasks.some(task => task.id === preferredId)) picker.append(option(preferredId, `任务 ${preferredId.slice(0, 8)}`));
      picker.value = preferredId;
    }
    show('workspace-state', data.tasks.length ? '已读取保存的任务。分析完成不等于原建议已完成。' : '尚无材料分析任务。');
    if (picker.value) await loadWorkspaceTask(picker.value);
  } catch (error) {
    if (authenticated && sequence === workspaceSequence) show('workspace-state', `刷新失败，已显示内容可能过期：${error.message}`, true);
  }
}
async function loadWorkspaceTask(id) {
  if (!/^[0-9a-f]{32}$/.test(id)) return;
  const sequence = ++workspaceSequence;
  if (workspaceSelected !== id) {
    workspaceSelected = id; workspaceReportSequence++;
    $('workspace-detail').textContent = '';
    $('workspace-runs').replaceChildren();
    $('workspace-report').textContent = '';
  }
  try {
    const data = await request(`/api/workspace-tasks/${encodeURIComponent(id)}`);
    if (!authenticated || sequence !== workspaceSequence || workspaceSelected !== id) return;
    const task = data.task;
    if (task.id !== id) throw new Error('任务回执与所选目标不一致');
    $('workspace-detail').textContent = `${task.title}\nTask ${task.id}\n状态 ${task.status} · 版本 ${task.version}\n来源 ${JSON.stringify(task.source)}\n材料摘要 ${task.source_digest}\n要求 ${task.instructions}\n验收 ${task.acceptance}\n\n${task.context.map(item => `[${item.kind}] ${item.title}\n${item.text}`).join('\n\n')}`;
    $('workspace-runs').replaceChildren();
    task.runs.forEach(run => {
      const row = document.createElement('div');
      row.textContent = `Run ${run.id} · ${run.status}${run.model ? ' · ' + run.model : ''}${run.error ? ' · ' + run.error : ''} `;
      if (run.status === 'succeeded' && run.report) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'secondary-action'; button.textContent = '查看报告';
        button.addEventListener('click', () => loadWorkspaceReport(task.id, run.id));
        row.append(button);
      }
      $('workspace-runs').append(row);
    });
    show('workspace-state', `已读取 ${task.title}；${task.runs.length} 次运行，失败记录保留。`);
  } catch (error) {
    if (authenticated && sequence === workspaceSequence) show('workspace-state', `读取失败，已显示内容可能过期：${error.message}`, true);
  }
}
async function loadWorkspaceReport(taskId, runId) {
  if (!/^[0-9a-f]{32}$/.test(taskId) || !/^[0-9a-f]{32}$/.test(runId)) return;
  const sequence = ++workspaceReportSequence;
  try {
    const report = await request(`/api/workspace-tasks/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(runId)}/report`);
    if (!authenticated || workspaceSelected !== taskId || sequence !== workspaceReportSequence) return;
    $('workspace-report').textContent = report.text;
    show('workspace-state', `报告 Run ${runId} · SHA256 ${report.sha256} · 仅已提供材料，不代表原建议完成。`);
  } catch (error) {
    if (authenticated && workspaceSelected === taskId && sequence === workspaceReportSequence) show('workspace-state', `报告读取失败，保留上次结果：${error.message}`, true);
  }
}
$('workspace-tab').addEventListener('click', async () => { selectTab('workspace'); await initializeWorkspace($('workspace-picker').value); });
$('workspace-picker').addEventListener('change', () => { if ($('workspace-picker').value) loadWorkspaceTask($('workspace-picker').value); else clearWorkspaceState(); });
$('workspace-refresh').addEventListener('click', () => initializeWorkspace($('workspace-picker').value));
