/* finance-media-ai 工作台
 *
 * 原则：页面上每一个数字都来自 /api/*，没有示例数据、没有写死的假状态。
 * 接口失败就把错误原样显示（含 error.details.reason），不吞、不替换成"成功"。
 * 需要模型但没密钥的阶段，页面显示 BLOCKED 与下一步该做什么，而不是假装完成。
 */
'use strict';

const API = '/api';

/* ---------------------------------------------------------------- 基础工具 */

function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function api(path, options) {
  const init = Object.assign({ headers: { 'Content-Type': 'application/json' } }, options || {});
  let response;
  try {
    response = await fetch(API + path, init);
  } catch (networkError) {
    return { ok: false, status: 0, error: { code: 'network_error', message: String(networkError) } };
  }
  const text = await response.text();
  let body = null;
  if (text) {
    try { body = JSON.parse(text); } catch (_) { body = { raw: text }; }
  }
  if (!response.ok) {
    const error = (body && body.error) || { code: 'http_' + response.status, message: text || '请求失败' };
    return { ok: false, status: response.status, error };
  }
  return { ok: true, status: response.status, data: body };
}

function money(value) {
  const number = Number(value || 0);
  return '¥' + number.toFixed(number !== 0 && Math.abs(number) < 1 ? 6 : 2);
}

function when(value) {
  if (!value) return '—';
  return String(value).replace('T', ' ').slice(0, 19);
}

function statusTag(status) {
  const map = {
    running: 'info', completed: 'ok', succeeded: 'ok', verified: 'ok', ready: 'ok',
    paused: 'warn', pending: 'warn', blocked: 'warn', ready_: 'warn', unverified: 'warn',
    failed: 'bad', cancelled: 'bad', error: 'bad', missing_api_key: 'bad',
  };
  const cls = map[status] || 'info';
  return `<span class="tag ${cls}">${esc(status)}</span>`;
}

function table(columns, rows, emptyText) {
  if (!rows || !rows.length) {
    return `<div class="empty">${esc(emptyText || '没有记录')}</div>`;
  }
  const head = columns.map((col) => `<th class="${col.num ? 'num' : ''}">${esc(col.title)}</th>`).join('');
  const body = rows.map((row) => {
    const cells = columns.map((col) => {
      const raw = col.value ? col.value(row) : row[col.key];
      const content = col.raw ? raw : esc(raw === undefined ? '' : raw);
      return `<td class="${col.num ? 'num' : ''}">${content}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');
  return `<div class="scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function failure(result) {
  const err = result.error || {};
  const details = err.details ? JSON.stringify(err.details) : '';
  return `<div class="note bad"><b>接口返回 ${result.status || ''} ${esc(err.code || '')}</b><br>${esc(err.message || '')}
    ${details ? `<div class="mono muted">${esc(details)}</div>` : ''}</div>`;
}

let JOBS_CACHE = null;
// 任务页的自动刷新定时器：切页时必须清掉，否则会在别的页面上偷偷改 DOM。
let activePoll = null;
async function loadJobs(force) {
  if (JOBS_CACHE && !force) return JOBS_CACHE;
  const result = await api('/workspace/jobs');
  JOBS_CACHE = result.ok ? (result.data.jobs || []) : [];
  return JOBS_CACHE;
}

/* ------------------------------------------------------------------ 路由表 */

const ROUTES = [
  { path: '/', title: '首页', group: '总览', hint: '一眼看到系统与任务的真实状态', render: pageHome },
  { path: '/topics', title: '选题', group: '研究', hint: '内容生产的起点', render: pageTopics },
  { path: '/tasks', title: '研究任务', group: '研究', hint: '按流水线拆解、执行、人工闸门', render: pageTasks },
  { path: '/tasks/:id', title: '任务执行状态', group: '研究', hidden: true, render: pageTaskDetail },
  { path: '/search', title: '搜索结果', group: '研究', hint: '真实联网搜索，失败如实报告', render: pageSearch },
  { path: '/sources', title: '资料库', group: '研究', hint: '抓到的原始资料与可信度', render: pageSources },
  { path: '/evidence', title: '证据链', group: '研究', hint: '每条主张追溯到来源与原文', render: pageEvidence },
  { path: '/reports', title: '研究报告', group: '研究', hint: '规则汇编草稿，模型缺席时如实标注', render: pageReports },
  { path: '/planning', title: '内容策划', group: '生产', hint: '从报告到传播角度（需模型）', render: pagePlanning },
  { path: '/scripts', title: '脚本编辑', group: '生产', hint: '视频任务的脚本文本', render: pageScripts },
  { path: '/storyboard', title: '分镜', group: '生产', hint: '逐镜字段与运镜转场', render: pageStoryboard },
  { path: '/assets', title: '素材', group: '生产', hint: '授权、哈希与用途', render: pageAssets },
  { path: '/video', title: '视频预览', group: '生产', hint: '成片与质检结果', render: pageVideo },
  { path: '/review', title: '人工审核', group: '生产', hint: '被闸门挡住的阶段与证据复核', render: pageReview },
  { path: '/models', title: '模型设置', group: '系统', hint: '价目未知即拒绝调用', render: pageModels },
  { path: '/providers', title: '服务商设置', group: '系统', hint: '密钥是否存在（不显示明文）', render: pageProviders },
  { path: '/costs', title: '成本统计', group: '系统', hint: '来自真实调用记录', render: pageCosts },
  { path: '/settings', title: '系统设置', group: '系统', hint: '配置快照与 Agent 名册', render: pageSettings },
];

function matchRoute(hash) {
  const clean = (hash || '#/').replace(/^#/, '') || '/';
  const parts = clean.split('/').filter(Boolean);
  for (const route of ROUTES) {
    const routeParts = route.path.split('/').filter(Boolean);
    if (routeParts.length !== parts.length) continue;
    const params = {};
    let matched = true;
    for (let index = 0; index < routeParts.length; index += 1) {
      const token = routeParts[index];
      if (token.startsWith(':')) params[token.slice(1)] = decodeURIComponent(parts[index]);
      else if (token !== parts[index]) { matched = false; break; }
    }
    if (matched) return { route, params, path: clean };
  }
  return { route: ROUTES[0], params: {}, path: clean };
}

function buildNav() {
  const groups = new Map();
  for (const route of ROUTES) {
    if (route.hidden) continue;
    if (!groups.has(route.group)) groups.set(route.group, []);
    groups.get(route.group).push(route);
  }
  const html = [];
  for (const [group, routes] of groups) {
    html.push(`<div class="group">${esc(group)}</div>`);
    for (const route of routes) {
      html.push(`<a href="#${route.path}" data-path="${esc(route.path)}">${esc(route.title)}</a>`);
    }
  }
  document.getElementById('nav').innerHTML = html.join('');
}

function markActive(path) {
  document.querySelectorAll('#nav a').forEach((link) => {
    const target = link.getAttribute('data-path');
    const active = target === '/' ? path === '/' : path === target || path.startsWith(target + '/');
    link.classList.toggle('active', active);
  });
}

async function refreshHealth() {
  const result = await api('/system/status');
  const node = document.getElementById('health');
  if (!result.ok) {
    node.className = 'pill bad';
    node.textContent = '后端不可达';
    return;
  }
  const data = result.data;
  const tables = (data.database || {}).table_count;
  node.className = 'pill ok';
  node.textContent = `${data.version || 'v?'} · ${tables ?? '?'} 表 · ${data.app_env || ''}`;
}

/* -------------------------------------------------------------------- 页面 */

async function pageHome(view) {
  const [status, tasks, costs, jobs, reports] = await Promise.all([
    api('/system/status'),
    api('/research-tasks'),
    api('/system/costs'),
    loadJobs(),
    api('/research-reports'),
  ]);
  const features = status.ok ? status.data.features : {};
  const blocked = Object.entries(features || {})
    .filter(([, value]) => !value)
    .map(([key]) => key);

  view.innerHTML = `
    <div class="grid">
      <div class="card kpi"><span class="k">研究报告</span><span class="v">${reports.ok ? reports.data.total : '—'}</span>
        <span class="muted">规则汇编草稿，需人工/模型完善</span></div>
      <div class="card kpi"><span class="k">研究任务</span><span class="v">${tasks.ok ? tasks.data.total : '—'}</span>
        <span class="muted">含人工闸门与重试记录</span></div>
      <div class="card kpi"><span class="k">模型调用</span><span class="v">${costs.ok ? costs.data.model_calls : '—'}</span>
        <span class="muted">${costs.ok ? money(costs.data.model_cost_cny) : ''}</span></div>
      <div class="card kpi"><span class="k">视频任务目录</span><span class="v">${jobs.length}</span>
        <span class="muted">${jobs.filter((job) => job.has_output).length} 个有成片</span></div>
    </div>
    <div class="card">
      <h2>能力开关（真实状态，来自 config/settings.yaml）</h2>
      <div class="row">
        ${Object.entries(features || {}).map(([key, value]) =>
          `<span class="tag ${value ? 'ok' : 'warn'}">${esc(key)}: ${value ? 'on' : 'off'}</span>`).join('')}
      </div>
      ${blocked.length ? `<div class="note">未启用的开关：${esc(blocked.join('、'))}。
        未启用时相关阶段会明确返回 blocked，不会用假数据顶替。</div>` : ''}
    </div>
    <div class="card">
      <h2>下一步能做真事的地方</h2>
      <div class="row">
        <a class="btn" href="#/tasks">建一个研究任务</a>
        <a class="btn ghost" href="#/search">先试真实搜索</a>
        <a class="btn ghost" href="#/video">看已生成的成片</a>
        <a class="btn ghost" href="#/providers">检查模型密钥状态</a>
      </div>
      <p class="muted">真实搜索与抓取不需要密钥即可用；需要模型的阶段（分析、综述、脚本）
      在配好 <span class="mono">.env</span> 与 <span class="mono">config/models.yaml</span> 之前会停在人工审核。</p>
    </div>`;
}

async function pageTopics(view) {
  const result = await api('/topics?limit=50');
  view.innerHTML = `
    <div class="card">
      <h2>新建选题</h2>
      <form id="topic-form" class="form-grid">
        <label>标题<input name="title" required maxlength="200" placeholder="例如：商业银行是如何赚钱的" /></label>
        <label>栏目
          <select name="column_id" id="topic-columns"><option value="">（未指定）</option></select>
        </label>
        <label>摘要<textarea name="summary" rows="2" placeholder="一句话说明这条选题要回答什么"></textarea></label>
        <div class="row" style="align-items:flex-end"><button class="btn" type="submit">创建</button></div>
      </form>
      <div id="topic-msg"></div>
    </div>
    <div class="card"><h2>选题列表</h2>
      ${result.ok ? table([
        { title: 'ID', key: 'id', num: true },
        { title: '标题', key: 'title' },
        { title: '状态', value: (row) => statusTag(row.status || 'candidate'), raw: true },
        { title: '评分', key: 'score_total', num: true },
        { title: '创建时间', value: (row) => when(row.created_at) },
      ], result.data.items, '还没有选题') : failure(result)}
    </div>`;

  const columns = await api('/topics/columns');
  if (columns.ok) {
    const select = document.getElementById('topic-columns');
    for (const column of columns.data.items || columns.data || []) {
      const option = document.createElement('option');
      option.value = column.id;
      option.textContent = column.name;
      select.appendChild(option);
    }
  }

  document.getElementById('topic-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const payload = {
      title: form.get('title'),
      summary: form.get('summary') || null,
    };
    if (form.get('column_id')) payload.column_id = Number(form.get('column_id'));
    const created = await api('/topics', { method: 'POST', body: JSON.stringify(payload) });
    document.getElementById('topic-msg').innerHTML = created.ok
      ? `<div class="note ok">已创建选题 #${created.data.id}</div>`
      : failure(created);
    if (created.ok) route();
  });
}

async function pageTasks(view) {
  const result = await api('/research-tasks?limit=50');
  view.innerHTML = `
    <div class="card">
      <h2>建研究任务</h2>
      <form id="task-form" class="form-grid">
        <label>标题<input name="title" required maxlength="200" placeholder="研究主题" /></label>
        <label>研究目标<textarea name="objective" rows="2" placeholder="要回答的具体问题（越具体，检索词越准）"></textarea></label>
        <label>检索词（每行一个，留空则按关键词规则拆分）
          <textarea name="queries" rows="2" placeholder="商业银行 净息差"></textarea></label>
        <label>跳过的阶段（没有模型密钥时建议跳过）
          <input name="skip" value="topic_candidate,topic_scoring,research_plan" /></label>
        <div class="row" style="align-items:flex-end"><button class="btn" type="submit">创建并启动</button></div>
      </form>
      <div id="task-msg"></div>
    </div>
    <div class="card"><h2>任务列表</h2>
      ${result.ok ? table([
        { title: 'ID', key: 'id', num: true },
        { title: '标题', value: (row) => `<a href="#/tasks/${row.id}">${esc(row.title)}</a>`, raw: true },
        { title: '状态', value: (row) => statusTag(row.status), raw: true },
        { title: '花费', value: (row) => money(row.cost_spent_cny), num: true },
        { title: '原因', value: (row) => esc(row.failure_reason || '—') },
        { title: '更新时间', value: (row) => when(row.updated_at) },
      ], result.data.items, '还没有任务：上面建一个') : failure(result)}
    </div>`;

  document.getElementById('task-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const payload = {
      title: form.get('title'),
      objective: form.get('objective') || '',
      queries: String(form.get('queries') || '').split('\n').map((item) => item.trim()).filter(Boolean),
      skip: String(form.get('skip') || '').split(',').map((item) => item.trim()).filter(Boolean),
      skip_reason: '没有模型密钥，跳过需要模型的阶段',
      start: true,
    };
    const created = await api('/research-tasks', { method: 'POST', body: JSON.stringify(payload) });
    document.getElementById('task-msg').innerHTML = created.ok
      ? `<div class="note ok">已创建任务 #${created.data.id}，共 ${created.data.subtasks.length} 个阶段</div>`
      : failure(created);
    if (created.ok) location.hash = '#/tasks/' + created.data.id;
  });
}

async function pageTaskDetail(view, params) {
  const id = params.id;
  const [detail, events] = await Promise.all([
    api(`/research-tasks/${id}`),
    api(`/research-tasks/${id}/events?limit=50`),
  ]);
  if (!detail.ok) { view.innerHTML = failure(detail); return; }
  const task = detail.data;
  const plan = task.plan || {};
  const gates = plan.human_gates || [];

  view.innerHTML = `
    <div class="card">
      <h2>${esc(task.title)}</h2>
      <div class="row">
        ${statusTag(task.status)}
        <span class="tag info">阶段 ${task.subtasks.length}</span>
        <span class="tag">花费 ${money(task.cost_spent_cny)}</span>
        ${task.failure_reason ? `<span class="tag warn">${esc(task.failure_reason)}</span>` : ''}
      </div>
      <p class="muted">${esc(task.objective || '（未填写研究目标）')}</p>
      <div class="row">
        <button class="btn" id="run-task">推进执行</button>
        <button class="btn ghost" id="pause-task">暂停</button>
        <button class="btn ghost" id="resume-task">恢复</button>
        <button class="btn danger" id="cancel-task">取消</button>
      </div>
      <div id="task-action"></div>
      ${gates.length ? `<div class="note">人工闸门：${gates.map((gate) => esc(gate.name || gate.id)).join('、')}
        —— 走到闸门会暂停，等你确认后再继续。</div>` : ''}
      ${plan.skip_reason ? `<div class="note">本任务跳过了 ${esc((plan.skipped_stages || []).join('、'))}：${esc(plan.skip_reason)}</div>` : ''}
    </div>
    <div class="card"><h2>阶段进度</h2>
      ${table([
        { title: '#', key: 'sequence', num: true },
        { title: '阶段', key: 'task_type' },
        { title: '执行者', value: (row) => esc(row.assignee || '—') },
        { title: '状态', value: (row) => statusTag(row.status), raw: true },
        { title: '尝试', value: (row) => `${row.attempts}/${row.max_attempts}`, num: true },
        { title: '说明', value: (row) => esc(row.failure_reason || '') },
      ], task.subtasks)}
    </div>
    <div class="card"><h2>事件流</h2>
      ${events.ok ? table([
        { title: '#', key: 'event_seq', num: true },
        { title: '类型', key: 'event_type' },
        { title: '执行者', value: (row) => esc(row.actor || '—') },
        { title: '时间', value: (row) => when(row.created_at) },
      ], events.data.items) : failure(events)}
    </div>`;

  const output = document.getElementById('task-action');
  const act = async (path, label) => {
    output.innerHTML = `<div class="note">${esc(label)}…</div>`;
    const result = await api(path, { method: 'POST', body: JSON.stringify(path.endsWith('/run') ? { max_iterations: 3 } : {}) });
    if (!result.ok) { output.innerHTML = failure(result); return; }
    output.innerHTML = `<div class="note ok">${esc(label)}完成：${esc(result.data.status || result.data.task?.status || '')}</div>`;
    setTimeout(() => route(), 600);
  };
  document.getElementById('run-task').onclick = () => act(`/research-tasks/${id}/run`, '推进执行（真实搜索与抓取）');
  document.getElementById('pause-task').onclick = () => act(`/research-tasks/${id}/pause`, '暂停');
  document.getElementById('resume-task').onclick = () => act(`/research-tasks/${id}/resume`, '恢复');
  document.getElementById('cancel-task').onclick = () => act(`/research-tasks/${id}/cancel`, '取消');

  // 任务在跑的时候自动刷新进度：只换两张卡的内容，不动按钮与输出区。
  if (['created', 'planning', 'running'].includes(String(task.status))) {
    const progressCard = view.querySelectorAll('.card')[1];
    const eventsCard = view.querySelectorAll('.card')[2];
    activePoll = setInterval(async () => {
      const [fresh, freshEvents] = await Promise.all([
        api(`/research-tasks/${id}`),
        api(`/research-tasks/${id}/events?limit=50`),
      ]);
      if (!fresh.ok) return;
      const next = fresh.data;
      progressCard.innerHTML = `<h2>阶段进度</h2><div class="row">${statusTag(next.status)}
        <span class="tag info">花费 ${money(next.cost_spent_cny)}</span></div>` + table([
        { title: '#', key: 'sequence', num: true },
        { title: '阶段', key: 'task_type' },
        { title: '执行者', value: (row) => esc(row.assignee || '—') },
        { title: '状态', value: (row) => statusTag(row.status), raw: true },
        { title: '尝试', value: (row) => `${row.attempts}/${row.max_attempts}`, num: true },
        { title: '说明', value: (row) => esc(row.failure_reason || '') },
      ], next.subtasks);
      if (freshEvents.ok) {
        eventsCard.innerHTML = `<h2>事件流</h2>` + table([
          { title: '#', key: 'event_seq', num: true },
          { title: '类型', key: 'event_type' },
          { title: '执行者', value: (row) => esc(row.actor || '—') },
          { title: '时间', value: (row) => when(row.created_at) },
        ], freshEvents.data.items);
      }
      if (!['created', 'planning', 'running'].includes(String(next.status))) {
        clearInterval(activePoll);
        activePoll = null;
      }
    }, 4000);
  }
}

async function pageSearch(view) {
  view.innerHTML = `
    <div class="card">
      <h2>真实搜索</h2>
      <form id="search-form" class="form-grid">
        <label>检索词<input name="q" required value="商业银行 净息差" /></label>
        <label>限定站点（可空）<input name="site" placeholder="pbc.gov.cn" /></label>
        <div class="row" style="align-items:flex-end"><button class="btn" type="submit">搜索</button></div>
      </form>
      <p class="muted">走搜狗/360/bing 降级链路，结果会还原成真实目标地址；抓取前有 SSRF 与 robots 闸门。</p>
    </div>
    <div id="search-result"></div>`;

  document.getElementById('search-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const box = document.getElementById('search-result');
    box.innerHTML = '<div class="loading">搜索中…</div>';
    const query = new URLSearchParams({ q: form.get('q') || '', site: form.get('site') || '' });
    const result = await api('/workspace/search?' + query.toString());
    if (!result.ok) { box.innerHTML = failure(result); return; }
    const data = result.data;
    if (!data.ok) {
      box.innerHTML = `<div class="note bad"><b>搜索没有返回结果</b><br>${esc(data.message || '')}
        <div class="mono muted">${esc((data.reasons || []).join(' | '))}</div></div>`;
      return;
    }
    const hits = data.hits || [];
    box.innerHTML = `<div class="card"><h2>命中 ${hits.length} 条</h2>
      ${table([
        { title: '#', value: (row) => esc(row.rank), num: true },
        { title: '标题', value: (row) => `<a href="${esc(row.url)}" target="_blank" rel="noreferrer">${esc(row.title)}</a>`, raw: true },
        { title: '来源引擎', key: 'provider' },
        { title: '摘要', value: (row) => esc((row.snippet || '').slice(0, 120)) },
        { title: '真实地址', value: (row) => row.resolved_from ? '<span class="tag ok">已还原</span>' : '<span class="tag">原样</span>', raw: true },
      ], hits, '引擎都返回了空页，稍后再试或换关键词')}</div>`;
  });
}

async function pageSources(view) {
  const result = await api('/sources?limit=50');
  view.innerHTML = `<div class="card"><h2>资料库（抓取后自动入库）</h2>
    ${result.ok ? table([
      { title: 'ID', key: 'id', num: true },
      { title: '标题', value: (row) => row.url ? `<a href="${esc(row.url)}" target="_blank" rel="noreferrer">${esc(row.title)}</a>` : esc(row.title), raw: true },
      { title: '类型', key: 'source_type' },
      { title: '可信度', value: (row) => esc(row.credibility_level || 'unknown') },
      { title: '发布时间', value: (row) => when(row.published_at) },
      { title: '抓取时间', value: (row) => when(row.accessed_at) },
    ], result.data.items, '还没有资料：去研究任务里跑一次采集') : failure(result)}
  </div>`;
}

async function pageEvidence(view) {
  const reports = await api('/research-reports?limit=50');
  const items = reports.ok ? reports.data.items : [];
  const options = items.map((item) => `<option value="${item.id}">#${item.id} ${esc(item.title)}</option>`).join('');
  view.innerHTML = `
    <div class="card">
      <h2>证据链</h2>
      <div class="row">
        <label>选择报告<select id="evidence-report"><option value="">（全部/未选）</option>${options}</select></label>
        <button class="btn" id="evidence-load">加载证据</button>
      </div>
      <p class="muted">verified 表示该数据点在来源摘录里逐字命中；unverified 不代表造假，只代表还没核对。</p>
    </div>
    <div id="evidence-box"><div class="empty">选一份报告查看证据</div></div>`;

  document.getElementById('evidence-load').onclick = async () => {
    const reportId = document.getElementById('evidence-report').value;
    const box = document.getElementById('evidence-box');
    if (!reportId) { box.innerHTML = '<div class="empty">先选一份报告</div>'; return; }
    const result = await api(`/evidence?research_report_id=${reportId}&limit=100`);
    box.innerHTML = result.ok ? `<div class="card"><h2>${result.data.total} 条证据</h2>
      ${table([
        { title: 'ID', key: 'id', num: true },
        { title: '主张', value: (row) => esc((row.claim || '').slice(0, 90)) },
        { title: '状态', value: (row) => statusTag((row.verification_status || 'unverified').toLowerCase()), raw: true },
        { title: '来源', value: (row) => esc(row.source_title || row.source_id) },
        { title: '定位', value: (row) => esc(row.quote_locator || '—') },
      ], result.data.items)}</div>` : failure(result);
  };
}

async function pageReports(view) {
  const result = await api('/research-reports?limit=50');
  const items = result.ok ? result.data.items : [];
  view.innerHTML = `
    <div class="card"><h2>研究报告</h2>
      ${result.ok ? table([
        { title: 'ID', key: 'id', num: true },
        { title: '标题', key: 'title' },
        { title: '核验状态', value: (row) => statusTag((row.verification_status || 'unverified').toLowerCase()), raw: true },
        { title: '创建时间', value: (row) => when(row.created_at) },
        { title: '操作', value: (row) => `<a href="#/planning?report=${row.id}">用它做内容策划</a>
          <button class="btn ghost" data-video="${row.id}">建视频任务</button>`, raw: true },
      ], items, '还没有报告：跑一个研究任务') : failure(result)}
    </div>
    <div id="video-job-result"></div>
    <div id="report-detail"></div>`;

  if (!items.length) return;
  const output = document.getElementById('video-job-result');
  view.querySelectorAll('button[data-video]').forEach((button) => {
    button.onclick = async () => {
      const reportId = button.dataset.video;
      output.innerHTML = '<div class="note">正在把研究材料搬进任务目录…</div>';
      const response = await api(`/research-reports/${reportId}/video-job`, { method: 'POST', body: '{}' });
      if (!response.ok) { output.innerHTML = failure(response); return; }
      const job = response.data;
      output.innerHTML = `<div class="card">
        <h2>视频任务目录已建好</h2>
        <p><code>${esc(job.path)}</code></p>
        <div class="row">
          <span class="tag info">阶段 ${esc(job.stage)}</span>
          <span class="tag">证据 ${job.evidence_count} 条</span>
          <span class="tag">来源 ${job.source_count} 条</span>
          ${job.blocked ? `<span class="tag warn">阻塞：${esc(job.blocked_reason)}</span>` : '<span class="tag ok">模型已就绪</span>'}
        </div>
        <div class="note ${job.blocked ? 'warn' : 'ok'}">${esc(job.next_step || '')}</div>
        <p class="muted">脚本与分镜目录是空的：没有模型可用时不会生成模板文字冒充脚本，等你把模型配好再跑下一步。</p>
        <a href="#/video">去视频预览页看已有成片</a>
      </div>`;
    };
  });

  const detail = await api(`/research-reports/${items[0].id}`);
  if (detail.ok) {
    const report = detail.data;
    document.getElementById('report-detail').innerHTML = `<div class="card">
      <h2>${esc(report.title)}</h2>
      <p class="muted">${esc(report.summary || '')}</p>
      <h3>要点</h3>
      <pre>${esc(JSON.stringify(report.findings || [], null, 2))}</pre>
      <h3>局限</h3>
      <pre>${esc(typeof report.limitations === 'string' ? report.limitations : JSON.stringify(report.limitations || [], null, 2))}</pre>
    </div>`;
  }
}

async function pagePlanning(view) {
  const reports = await api('/research-reports?limit=50');
  const items = reports.ok ? reports.data.items : [];
  const options = items.map((item) => `<option value="${item.id}">#${item.id} ${esc(item.title)}</option>`).join('');
  view.innerHTML = `
    <div class="card">
      <h2>内容策划</h2>
      <div class="row">
        <label>从哪份报告出发<select id="plan-report">${options || '<option value="">（暂无报告）</option>'}</select></label>
      </div>
      <p class="muted">策划要点需要模型做取舍与角度判断。没有模型密钥时这一步是 BLOCKED，
        不要把"规则汇编"直接当策划结论。</p>
      <div class="note">BLOCKED 的解除条件：<span class="mono">.env</span> 里配 <span class="mono">DEEPSEEK_API_KEY</span>，
        并在 <span class="mono">config/models.yaml</span> 里启用模型、填好每千 token 的输入/输出价目（未填价目会被拒绝调用）。</div>
      <div id="plan-out"></div>
    </div>`;

  const select = document.getElementById('plan-report');
  const out = document.getElementById('plan-out');
  const render = async () => {
    if (!select.value) { out.innerHTML = ''; return; }
    const detail = await api(`/research-reports/${select.value}`);
    if (!detail.ok) { out.innerHTML = failure(detail); return; }
    const report = detail.data;
    const findings = Array.isArray(report.findings) ? report.findings : [];
    out.innerHTML = `<h3>这份报告能支撑什么</h3>
      <ul>${findings.slice(0, 8).map((item) => `<li>${esc(typeof item === 'string' ? item : (item.text || JSON.stringify(item)))}</li>`).join('') || '<li class="muted">没有要点</li>'}</ul>
      <h3>还缺什么（人工判断）</h3>
      <p class="muted">结论角度、反方证据、失效条件、观众的"为什么现在关心"——这些需要模型或人来写，
      系统只提供可核对的素材与数字。</p>`;
  };
  select.onchange = render;
  if (select.value) render();
}

async function jobPicker(currentId) {
  const jobs = await loadJobs(true);
  if (!jobs.length) return { html: '<div class="empty">content/jobs 下还没有视频任务目录</div>', jobs: [] };
  const options = jobs.map((job) => `<option value="${job.job_id}" ${job.job_id === currentId ? 'selected' : ''}>${esc(job.job_id)}${job.has_output ? ' ✅' : ''}</option>`).join('');
  return {
    jobs,
    html: `<div class="row"><label>视频任务<select id="job-select">${options}</select></label></div>`,
  };
}

function jobFromQuery() {
  const query = new URLSearchParams(location.hash.split('?')[1] || '');
  return query.get('job') || '';
}

async function pageScripts(view) {
  const picker = await jobPicker(jobFromQuery());
  view.innerHTML = `<div class="card"><h2>脚本</h2>${picker.html}<div id="script-box"></div></div>`;
  const box = document.getElementById('script-box');
  const load = async (jobId) => {
    if (!jobId) { box.innerHTML = ''; return; }
    const result = await api(`/workspace/jobs/${jobId}`);
    if (!result.ok) { box.innerHTML = failure(result); return; }
    const data = result.data;
    box.innerHTML = data.script
      ? `<h3>script.md</h3><pre>${esc(data.script)}</pre>`
      : '<div class="empty">该任务目录里没有 script/script.md</div>';
  };
  const select = document.getElementById('job-select');
  if (select) {
    select.onchange = () => { location.hash = `#/scripts?job=${select.value}`; };
    load(select.value);
  }
}

async function pageStoryboard(view) {
  const picker = await jobPicker(jobFromQuery());
  view.innerHTML = `<div class="card"><h2>分镜</h2>${picker.html}<div id="sb-box"></div></div>`;
  const box = document.getElementById('sb-box');
  const load = async (jobId) => {
    if (!jobId) { box.innerHTML = ''; return; }
    const result = await api(`/workspace/jobs/${jobId}`);
    if (!result.ok) { box.innerHTML = failure(result); return; }
    const data = result.data;
    if (!data.storyboard) { box.innerHTML = '<div class="empty">没有 storyboard/storyboard.csv</div>'; return; }
    const columns = data.storyboard.columns.map((name) => ({
      title: name,
      value: (row) => String(row[name] || '').slice(0, 60),
    }));
    box.innerHTML = `<h3>${data.storyboard.total} 个镜头</h3>${table(columns, data.storyboard.rows)}
      ${data.storyboard.truncated ? '<p class="muted">表格已截断显示</p>' : ''}`;
  };
  const select = document.getElementById('job-select');
  if (select) {
    select.onchange = () => { location.hash = `#/storyboard?job=${select.value}`; };
    load(select.value);
  }
}

async function pageAssets(view) {
  const picker = await jobPicker(jobFromQuery());
  view.innerHTML = `<div class="card"><h2>素材授权与来源</h2>${picker.html}<div id="asset-box"></div></div>`;
  const box = document.getElementById('asset-box');
  const load = async (jobId) => {
    if (!jobId) { box.innerHTML = ''; return; }
    const result = await api(`/workspace/jobs/${jobId}`);
    if (!result.ok) { box.innerHTML = failure(result); return; }
    const data = result.data;
    const manifest = data.assets ? table([
      { title: '素材', key: 'asset_id' },
      { title: '类别', key: 'asset_class' },
      { title: '类型', key: 'kind' },
      { title: '许可', key: 'license' },
      { title: '可商用', value: (row) => statusTag(row.commercial_use === 'allowed' ? 'ok' : 'unclear'), raw: true },
      { title: '署名', value: (row) => esc((row.credit || '').slice(0, 70)) },
      { title: '用于镜头', key: 'used_in_shots' },
    ], data.assets.rows, '没有 assets_manifest.csv') : '<div class="empty">没有素材清单</div>';
    const sources = data.sources ? table([
      { title: 'ID', key: 'source_id' },
      { title: '名称', key: 'source_name' },
      { title: '发布方', key: 'publisher' },
      { title: '许可', key: 'license' },
      { title: 'SHA-256', value: (row) => esc(String(row.original_file_hash || '').slice(0, 16)) },
    ], data.sources.rows) : '';
    box.innerHTML = `<h3>素材清单</h3>${manifest}<h3>已批准来源登记</h3>${sources}`;
  };
  const select = document.getElementById('job-select');
  if (select) {
    select.onchange = () => { location.hash = `#/assets?job=${select.value}`; };
    load(select.value);
  }
}

async function pageVideo(view) {
  const jobs = await loadJobs(true);
  const withOutput = jobs.filter((job) => job.has_output);
  if (!withOutput.length) {
    view.innerHTML = '<div class="card"><h2>视频预览</h2><div class="empty">还没有成片。视频渲染由 content/jobs 下的构建脚本产出。</div></div>';
    return;
  }
  const chosen = jobFromQuery() || withOutput[0].job_id;
  const job = withOutput.find((item) => item.job_id === chosen) || withOutput[0];
  const file = job.outputs && job.outputs[0] ? job.outputs[0] : null;
  const detail = await api(`/workspace/jobs/${job.job_id}`);
  const qc = detail.ok && detail.data.qc_report ? detail.data.qc_report : '';

  view.innerHTML = `
    <div class="card">
      <h2>${esc(job.job_id)}</h2>
      <div class="row">
        ${withOutput.map((item) => `<a class="btn ${item.job_id === job.job_id ? '' : 'ghost'}" href="#/video?job=${item.job_id}">${esc(item.job_id)}</a>`).join('')}
      </div>
      ${file ? `<p class="muted">${esc(file.name)} · ${(file.byte_size / 1048576).toFixed(2)} MB</p>
      <video controls preload="metadata" src="${API}/workspace/jobs/${encodeURIComponent(job.job_id)}/file?path=${encodeURIComponent('output/' + file.name)}"></video>` : '<div class="empty">没有 MP4</div>'}
    </div>
    <div class="card"><h2>质检报告</h2>
      ${qc ? `<pre>${esc(qc)}</pre>` : '<div class="empty">没有 qc/qc_report.md</div>'}
    </div>
    <div class="card"><h2>重新生成时间轴</h2>
      <p class="muted">读取该任务的 storyboard/storyboard.csv 与素材清单，交给引擎校验并写出时间轴。
        默认只做时间轴（秒级）；真渲染一条长片要十几分钟，需要用命令行或等待后续排期。</p>
      <div class="row"><button class="btn" id="build-timeline">生成时间轴</button></div>
      <div id="pipeline-out"></div>
    </div>`;

  document.getElementById('build-timeline').onclick = async () => {
    const out = document.getElementById('pipeline-out');
    out.innerHTML = '<div class="note">正在校验分镜并把素材交给引擎…</div>';
    const result = await api(`/workspace/jobs/${job.job_id}/pipeline`, {
      method: 'POST',
      body: JSON.stringify({ render: false }),
    });
    if (!result.ok) { out.innerHTML = failure(result); return; }
    const data = result.data;
    out.innerHTML = data.blocked
      ? `<div class="note warn">${esc(data.message || data.blocked_reason || '被阻塞')}</div>`
      : `<div class="note ok">时间轴已写入：${esc(data.timeline_path || '')}
          · ${data.storyboard_shots} 个镜头 · ${data.frames} 帧 · ${data.canvas.w}×${data.canvas.h}</div>
         <pre>${esc((data.engine_calls || []).join('\n'))}</pre>`;
  };
}

async function pageReview(view) {
  const tasks = await api('/research-tasks?status_filter=paused&limit=50');
  const items = tasks.ok ? tasks.data.items : [];
  const rows = items.map((task) => ({
    id: task.id,
    title: task.title,
    reason: task.failure_reason || '',
    at: task.updated_at,
  }));
  view.innerHTML = `
    <div class="card">
      <h2>待人工审核（暂停中的任务）</h2>
      ${tasks.ok ? table([
        { title: 'ID', value: (row) => `<a href="#/tasks/${row.id}">#${row.id}</a>`, raw: true },
        { title: '标题', key: 'title' },
        { title: '暂停原因', value: (row) => esc(row.reason || '—') },
        { title: '更新时间', value: (row) => when(row.at) },
      ], rows, '没有暂停中的任务') : failure(tasks)}
      <p class="muted">暂停通常意味着"需要一个我还没有的东西"：模型密钥、脚本确认、事实核查确认。
      在任务详情页可以恢复、取消或查看事件流。</p>
    </div>
    <div class="card">
      <h2>审核清单（人工判断项）</h2>
      <ul class="muted">
        <li>证据是否逐字命中来源（不是模糊相似）</li>
        <li>关键数字是否带单位与口径（同比/环比、亿元/万亿元）</li>
        <li>结论里有没有把"推断"写成"事实"</li>
        <li>成片的署名、授权、字幕与素材是否一致</li>
      </ul>
    </div>`;
}

async function pageModels(view) {
  const config = await api('/workspace/config');
  if (!config.ok) { view.innerHTML = failure(config); return; }
  const models = config.data.models || [];
  view.innerHTML = `
    <div class="card"><h2>模型与价目</h2>
      ${table([
        { title: '服务商', key: 'provider' },
        { title: '模型', key: 'model' },
        { title: '启用', value: (row) => row.enabled ? statusTag('ok') : statusTag('off'), raw: true },
        { title: '输入价/1K', value: (row) => row.input_price_per_1k_cny === null ? '<span class="tag warn">未配置</span>' : esc(row.input_price_per_1k_cny), raw: true, num: true },
        { title: '输出价/1K', value: (row) => row.output_price_per_1k_cny === null ? '<span class="tag warn">未配置</span>' : esc(row.output_price_per_1k_cny), raw: true, num: true },
        { title: '价目状态', key: 'pricing_status' },
      ], models)}
      <div class="note">价目未知的模型会被<b>直接拒绝调用</b>（pricing_policy=${esc(config.data.cost.pricing_policy)}），
        不会按 0 元记账。改价目请编辑 <span class="mono">config/models.yaml</span>（本页只读）。</div>
    </div>
    <div class="card"><h2>预算</h2>
      <div class="grid">
        <div class="kpi"><span class="k">每日上限</span><span class="v small">${esc(config.data.cost.daily_cap_cny)} ${esc(config.data.cost.currency)}</span></div>
        <div class="kpi"><span class="k">单任务调用上限</span><span class="v small">${esc(config.data.cost.default_task_calls)}</span></div>
        <div class="kpi"><span class="k">单任务成本上限</span><span class="v small">${esc(config.data.cost.default_task_cost_cny)}</span></div>
        <div class="kpi"><span class="k">并发</span><span class="v small">${esc(config.data.execution.max_concurrency)}</span></div>
      </div>
    </div>`;
}

async function pageProviders(view) {
  const result = await api('/system/providers');
  if (!result.ok) { view.innerHTML = failure(result); return; }
  view.innerHTML = `<div class="card"><h2>模型服务商</h2>
    ${table([
      { title: '服务商', key: 'name' },
      { title: '状态', value: (row) => statusTag(row.status), raw: true },
      { title: '启用', value: (row) => row.enabled ? '是' : '否' },
      { title: '密钥环境变量', value: (row) => `<span class="mono">${esc(row.key_env || '—')}</span>`, raw: true },
      { title: '接口地址', value: (row) => esc(row.base_url || '—') },
      { title: '说明', value: (row) => esc(row.note || '') },
    ], result.data)}
    <div class="note">这里只显示"密钥是否存在"，不显示密钥内容。缺密钥时模型阶段会 blocked，
      不会自动切到假数据。</div>
  </div>`;
}

async function pageCosts(view) {
  const result = await api('/system/costs');
  if (!result.ok) { view.innerHTML = failure(result); return; }
  const data = result.data;
  view.innerHTML = `
    <div class="grid">
      <div class="card kpi"><span class="k">模型调用</span><span class="v">${data.model_calls}</span>
        <span class="muted">${data.model_tokens} tokens</span></div>
      <div class="card kpi"><span class="k">模型成本</span><span class="v">${money(data.model_cost_cny)}</span>
        <span class="muted">价目未知 ${data.pricing_unknown_calls} 次</span></div>
      <div class="card kpi"><span class="k">工具调用</span><span class="v">${data.tool_calls}</span>
        <span class="muted">${money(data.tool_cost_cny)}</span></div>
      <div class="card kpi"><span class="k">任务累计花费</span><span class="v">${money(data.task_cost_cny)}</span></div>
    </div>
    <div class="card"><h2>按模型</h2>
      ${table([
        { title: '模型', key: 'key' },
        { title: '调用', key: 'calls', num: true },
        { title: 'tokens', key: 'tokens', num: true },
        { title: '成本', value: (row) => money(row.cost_cny), num: true },
      ], data.by_model, '还没有模型调用')}
    </div>
    <div class="card"><h2>按工具</h2>
      ${table([
        { title: '工具', key: 'key' },
        { title: '调用', key: 'calls', num: true },
        { title: '成本', value: (row) => money(row.cost_cny), num: true },
      ], data.by_tool, '还没有工具调用')}
    </div>
    <div class="card"><h2>按天</h2>
      ${table([
        { title: '日期', key: 'key' },
        { title: '调用', key: 'calls', num: true },
        { title: 'tokens', key: 'tokens', num: true },
        { title: '成本', value: (row) => money(row.cost_cny), num: true },
      ], data.by_day, '没有按天数据')}
      <div class="note">${esc(data.note)}</div>
    </div>`;
}

async function pageSettings(view) {
  const [status, config, agents] = await Promise.all([
    api('/system/status'),
    api('/workspace/config'),
    api('/system/agents'),
  ]);
  const database = status.ok ? status.data.database || {} : {};
  view.innerHTML = `
    <div class="card"><h2>运行状态</h2>
      ${status.ok ? `<div class="grid">
        <div class="kpi"><span class="k">版本</span><span class="v small">${esc(status.data.version)}</span></div>
        <div class="kpi"><span class="k">环境</span><span class="v small">${esc(status.data.app_env)}</span></div>
        <div class="kpi"><span class="k">数据表</span><span class="v small">${esc(database.table_count ?? '?')}</span></div>
        <div class="kpi"><span class="k">迁移版本</span><span class="v small mono">${esc(database.migration_revision || '—')}</span></div>
        <div class="kpi"><span class="k">journal_mode</span><span class="v small">${esc(database.journal_mode || '—')}</span></div>
        <div class="kpi"><span class="k">busy_timeout</span><span class="v small">${esc(database.busy_timeout || '—')}</span></div>
      </div>` : failure(status)}
    </div>
    <div class="card"><h2>执行参数</h2>
      ${config.ok ? `<div class="grid">
        <div class="kpi"><span class="k">执行开关</span><span class="v small">${config.data.execution.enabled ? 'on' : 'off'}</span></div>
        <div class="kpi"><span class="k">租约秒数</span><span class="v small">${config.data.execution.lease_seconds}</span></div>
        <div class="kpi"><span class="k">心跳秒数</span><span class="v small">${config.data.execution.heartbeat_seconds}</span></div>
      </div>
      <p class="muted">配置文件：<span class="mono">${esc(config.data.editable_by)}</span></p>` : failure(config)}
    </div>
    <div class="card"><h2>Agent 名册（${agents.ok ? agents.data.count : '?'}）</h2>
      ${agents.ok ? table([
        { title: '角色', value: (row) => `<span class="mono">${esc(row.key)}</span>`, raw: true },
        { title: '名称', key: 'name' },
        { title: '可接任务', value: (row) => esc((row.task_types || []).join('、')) },
        { title: '工具', value: (row) => esc((row.tools || []).join('、')) },
        { title: '数据范围', value: (row) => esc((row.db_scopes || []).join('、')) },
      ], agents.data.agents) : failure(agents)}
    </div>`;
}

/* ------------------------------------------------------------------ 启动 */

async function route() {
  if (activePoll) {
    clearInterval(activePoll);
    activePoll = null;
  }
  const { route: matched, params, path } = matchRoute(location.hash);
  document.getElementById('page-title').textContent = matched.title;
  document.getElementById('page-hint').textContent = matched.hint || '';
  markActive(path);
  const view = document.getElementById('view');
  view.innerHTML = '<div class="loading">加载中…</div>';
  try {
    await matched.render(view, params);
  } catch (error) {
    view.innerHTML = `<div class="note bad"><b>页面渲染失败</b><br>${esc(String(error))}</div>`;
  }
}

document.getElementById('refresh').addEventListener('click', () => {
  JOBS_CACHE = null;
  refreshHealth();
  route();
});
window.addEventListener('hashchange', route);

buildNav();
refreshHealth();
route();
