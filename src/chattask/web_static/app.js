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
  } catch (e) { show('task-state', '无法确认登录状态，请刷新后重试。', true); }
})();
