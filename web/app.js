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

/**
 * `<input type="datetime-local">` 要的是**本地**时间字符串。
 * 以前两处都写 `new Date(...).toISOString().slice(0, 16)`，那是 UTC ——
 * 在北京时间下午打开页面，默认会变成"第二天凌晨 5 点"。
 */
function localInputValue(date) {
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
    + `T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function tomorrowEvening() {
  const date = new Date(Date.now() + 86400000);
  date.setHours(20, 0, 0, 0);
  return localInputValue(date);
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
  { path: '/checks', title: '语义核查', group: '研究', hint: '模型逐条判定，人工再复核一遍', render: pageChecks },
  { path: '/reports', title: '研究报告', group: '研究', hint: '规则汇编草稿，模型缺席时如实标注', render: pageReports },
  { path: '/planning', title: '内容策划', group: '生产', hint: '从报告到传播角度（需模型）', render: pagePlanning },
  { path: '/scripts', title: '脚本编辑', group: '生产', hint: '视频任务的脚本文本', render: pageScripts },
  { path: '/storyboard', title: '分镜', group: '生产', hint: '逐镜字段与运镜转场', render: pageStoryboard },
  { path: '/assets', title: '素材', group: '生产', hint: '授权、哈希与用途', render: pageAssets },
  { path: '/video', title: '视频预览', group: '生产', hint: '成片与质检结果', render: pageVideo },
  { path: '/review', title: '人工审核', group: '生产', hint: '被闸门挡住的阶段与证据复核', render: pageReview },
  { path: '/operations', title: '内容日历', group: '运营', hint: '排期、发布留痕、平台数据回填', render: pageOperations },
  { path: '/models', title: '模型设置', group: '系统', hint: '价目未知即拒绝调用', render: pageModels },
  { path: '/providers', title: '服务商设置', group: '系统', hint: '密钥是否存在（不显示明文）', render: pageProviders },
  { path: '/costs', title: '成本统计', group: '系统', hint: '来自真实调用记录', render: pageCosts },
  { path: '/settings', title: '系统设置', group: '系统', hint: '配置快照与 Agent 名册', render: pageSettings },
];

function matchRoute(hash) {
  // 先切掉查询串再匹配：`#/checks?report=1` 的路径是 `/checks`，`?report=1` 是给页面自己的参数。
  // （早先直接拿整段 hash 按 `/` 切，于是任何带查询串的深链都会掉回首页。）
  const raw = (hash || '#/').replace(/^#/, '') || '/';
  const clean = raw.split('?')[0] || '/';
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

function dailyCard(data) {
  const lines = (data.attention || []).map((item) => `<li>${esc(item)}</li>`).join('');
  const today = data.cost && data.cost.today ? data.cost.today : { calls: 0, cost_cny: 0 };
  const window = data.cost && data.cost.window ? data.cost.window : { calls: 0, cost_cny: 0 };
  const backup = data.backup || {};
  const disk = data.disk || {};
  const pending = (data.checks && data.checks.pending) || 0;
  return `<div class="card">
      <h2>今天的体检 <span class="tag ${data.ok ? 'ok' : 'bad'}">${data.ok ? '没有待办' : `${(data.attention || []).length} 件要处理`}</span></h2>
      <div class="row">
        <span class="tag">今天模型调用 ${today.calls} 次 / ${money(today.cost_cny)}</span>
        <span class="tag">近 ${data.window_days} 天 ${window.calls} 次 / ${money(window.cost_cny)}</span>
        <span class="tag ${pending ? 'warn' : ''}">待人工确认 ${pending} 条</span>
        <span class="tag">暂停任务 ${(data.tasks.by_status || {}).paused || 0} 个</span>
        <span class="tag ${backup.age_days == null || backup.age_days > 7 ? 'warn' : 'ok'}">最近备份 ${backup.age_days == null ? '没有' : `${backup.age_days} 天前`}</span>
        <span class="tag ${disk.ok ? 'ok' : 'warn'}">磁盘 ${disk.ok ? `${disk.free_gb} GB 可用` : '读不到'}</span>
      </div>
      ${lines ? `<ul class="list">${lines}</ul>` : '<p class="muted">今天没有需要处理的事。</p>'}
      <div class="row">
        <a class="btn ghost" href="#/costs">看花费明细</a>
        <a class="btn ghost" href="#/tasks">看任务</a>
        <a class="btn ghost" href="#/checks">去确认核查</a>
      </div>
      <p class="muted">这条卡片和 <span class="mono">python -m backend.scripts.daily_check</span> 是同一套判断逻辑
      （${esc(data.generated_at || '')} 生成），所以命令行和页面不会打架。</p>
    </div>`;
}

async function pageHome(view) {
  const [status, tasks, costs, jobs, reports, daily] = await Promise.all([
    api('/system/status'),
    api('/research-tasks'),
    api('/system/costs'),
    loadJobs(),
    api('/research-reports'),
    api('/system/daily?days=7'),
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
    ${daily.ok ? dailyCard(daily.data) : `<div class="card"><h2>今天的体检</h2><p class="muted">读不到体检数据：${esc(daily.error || '接口没返回')}</p></div>`}
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
    </div>
    <div class="card" id="topic-suggest-box">
      <h2>选题建议 <span class="dim">（规则推荐，不联网、不花钱）</span></h2>
      <div class="row">
        <label>看最近
          <select id="suggest-window">
            <option value="7">7 天</option>
            <option value="30" selected>30 天</option>
            <option value="90">90 天</option>
          </select>
        </label>
        <button class="btn ghost" id="suggest-refresh">重新计算</button>
      </div>
      <div id="suggest-msg" class="note">数据来自已经抓到的来源；分数只用来排序，采纳与否由你定。</div>
      <div id="suggest-list"></div>
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

  loadTopicSuggestions();

  document.getElementById('suggest-refresh').addEventListener('click', () => loadTopicSuggestions());
  document.getElementById('suggest-window').addEventListener('change', () => loadTopicSuggestions());
}

async function loadTopicSuggestions() {
  const box = document.getElementById('suggest-list');
  const msg = document.getElementById('suggest-msg');
  if (!box) return;
  const windowDays = document.getElementById('suggest-window')?.value || '30';
  box.innerHTML = '<div class="note">正在算…</div>';
  const result = await api(`/topics/suggestions?window_days=${windowDays}&limit=10`);
  if (!result.ok) {
    box.innerHTML = failure(result);
    return;
  }
  const data = result.data;
  msg.innerHTML = `扫了 <b>${data.scanned}</b> 条已入库来源，窗口 ${data.window_days} 天，最低分 ${data.min_score}。${esc(data.note || '')}`;
  if (!data.items?.length) {
    box.innerHTML = '<div class="note">这段时间里没有够格的候选：要么来源太少，要么都已做过。先去「搜索」页抓一批资料。</div>';
    return;
  }
  box.innerHTML = data.items.map((item, index) => `
    <div class="alert ok" style="margin:10px 0">
      <div class="row" style="justify-content:space-between;align-items:flex-start">
        <b>${esc(item.title)}</b>
        <span class="tag">${item.score} 分</span>
      </div>
      <div class="note">${item.tier_label} · ${item.freshness_label}${item.age_days === null ? '' : ` · ${item.age_days} 天前`}${item.corroborated ? ' · 多家报道' : ''}${item.collections?.length ? ` · 合集 ${esc(item.collections.join('、'))}` : ''}</div>
      <ul class="note" style="margin:6px 0 8px 16px">${(item.reasons || []).map((line) => `<li>${esc(line)}</li>`).join('')}</ul>
      <div class="row">
        <button class="btn" data-adopt="${index}">采纳为选题</button>
        ${item.urls?.length ? `<a class="dim" href="${esc(item.urls[0])}" target="_blank" rel="noreferrer">看来源（${item.urls.length}）</a>` : ''}
      </div>
    </div>`).join('');
  box.querySelectorAll('[data-adopt]').forEach((button) => {
    button.addEventListener('click', async () => {
      const item = data.items[Number(button.dataset.adopt)];
      button.disabled = true;
      const created = await api('/topics', { method: 'POST', body: JSON.stringify({ title: item.title }) });
      if (created.ok) {
        msg.innerHTML = `<div class="note ok">已采纳为选题 #${created.data.id}，可以到「研究任务」页为它建任务。</div>`;
        route();
      } else {
        msg.innerHTML = failure(created);
        button.disabled = false;
      }
    });
  });
}

async function pageTasks(view) {
  // 深链参数放在 hash 里：`#/tasks?offset=50&status=failed`
  const query = new URLSearchParams((location.hash.split('?')[1] || ''));
  const offset = Math.max(0, Number(query.get('offset') || 0) || 0);
  const status = query.get('status') || '';
  const limit = 20;
  const suffix = `limit=${limit}&offset=${offset}${status ? `&status=${encodeURIComponent(status)}` : ''}`;
  const result = await api(`/research-tasks?${suffix}`);
  const total = result.ok ? Number(result.data.total || 0) : 0;
  const shown = result.ok ? result.data.items.length : 0;
  const from = shown === 0 ? 0 : offset + 1;
  const to = offset + shown;
  const statusOptions = [
    ['', '全部状态'],
    ['created', '已创建'],
    ['running', '运行中'],
    ['paused', '暂停（等人）'],
    ['completed', '已完成'],
    ['failed', '失败'],
    ['cancelled', '已取消'],
  ];
  const pager = `
      <div class="row" style="justify-content:space-between;align-items:center;margin:8px 0">
        <div class="row">
          <label style="margin:0">状态
            <select id="task-status">${statusOptions
              .map(([value, label]) => `<option value="${value}"${value === status ? ' selected' : ''}>${label}</option>`)
              .join('')}</select>
          </label>
        </div>
        <div class="row">
          <span class="note">第 ${from}–${to} 条 / 共 ${total} 条</span>
          <a class="btn ghost${offset === 0 ? ' disabled' : ''}" href="#/tasks?offset=${Math.max(0, offset - limit)}${status ? `&status=${status}` : ''}">上一页</a>
          <a class="btn ghost${to >= total ? ' disabled' : ''}" href="#/tasks?offset=${offset + limit}${status ? `&status=${status}` : ''}">下一页</a>
        </div>
      </div>`;
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
      ${result.ok ? pager : ''}
      ${result.ok ? table([
        { title: 'ID', key: 'id', num: true },
        { title: '标题', value: (row) => `<a href="#/tasks/${row.id}">${esc(row.title)}</a>`, raw: true },
        { title: '状态', value: (row) => statusTag(row.status), raw: true },
        { title: '花费', value: (row) => money(row.cost_spent_cny), num: true },
        { title: '原因', value: (row) => esc(row.failure_reason || '—') },
        { title: '更新时间', value: (row) => when(row.updated_at) },
      ], result.data.items, status ? '这个状态下没有任务' : '还没有任务：上面建一个') : failure(result)}
    </div>`;

  const statusSelect = document.getElementById('task-status');
  if (statusSelect) {
    statusSelect.addEventListener('change', () => {
      const value = statusSelect.value;
      location.hash = `#/tasks?offset=0${value ? `&status=${value}` : ''}`;
    });
  }

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
  const planning = task.planning || null;
  const byModel = Boolean(planning && planning.model_used);
  const planningCard = planning
    ? `<div class="card"><h2>研究规划</h2>
        <div class="row">
          <span class="tag ${byModel ? 'ok' : 'warn'}">${byModel ? '模型规划' : '规则拆词'}</span>
          ${planning.model_used === false ? '<span class="tag">本轮没有调用模型</span>' : ''}
          <span class="tag info">检索词 ${planning.query_count} 个</span>
          <span class="tag info">规划子任务 ${planning.subtask_count} 个</span>
        </div>
        ${planning.degraded_reason ? `<div class="note warn">降级原因：<code>${esc(planning.degraded_reason)}</code>${planning.degraded_message ? ` —— ${esc(planning.degraded_message)}` : ''}</div>` : ''}
        ${(planning.core_questions || []).length ? `<div class="note">核心问题：<ol>${planning.core_questions.map((item) => `<li>${esc(item)}</li>`).join('')}</ol></div>` : ''}
        ${(planning.risks || []).length ? `<div class="note">风险与缺口：<ul>${planning.risks.map((item) => `<li>${esc(item)}</li>`).join('')}</ul></div>` : ''}
        ${(planning.proposed_subtasks || []).length ? `<div class="note">由规划动态追加的检索任务：<ul>${planning.proposed_subtasks.map((item) => `<li>${esc(item.question || '')}</li>`).join('')}</ul></div>` : ''}
      </div>`
    : '';

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
    ${planningCard}
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

const VERDICT_LABEL = {
  supported: '支持',
  partially_supported: '部分支持',
  contradicted: '与证据冲突',
  insufficient_evidence: '证据不足',
  unverifiable: '无法判断',
};

const HUMAN_LABEL = {
  pending: '待人工复核',
  confirmed: '人工已确认',
  rejected: '人工已驳回',
  needs_more_evidence: '需补证据',
};

function verdictTag(verdict) {
  const map = { supported: 'ok', partially_supported: 'info', contradicted: 'bad', insufficient_evidence: 'warn', unverifiable: '' };
  const tone = map[verdict] || '';
  return `<span class="tag ${tone}">${esc(VERDICT_LABEL[verdict] || verdict)}</span>`;
}

function humanTag(status) {
  const tone = status === 'confirmed' ? 'ok' : status === 'rejected' ? 'danger' : status === 'needs_more_evidence' ? 'warn' : 'info';
  return `<span class="tag ${tone}">${esc(HUMAN_LABEL[status] || status)}</span>`;
}

function quoteList(items) {
  const list = Array.isArray(items) ? items : [];
  if (!list.length) return '<span class="muted">—</span>';
  return list.map((item) => `<div class="quote">${esc(item)}</div>`).join('');
}

// 视频任务卡上的成稿闸门提示：任务是不是还在用"人工已确认"的结论
function gateBanner(gate) {
  if (!gate || (!gate.rule && !gate.check_ids)) return '';
  const ids = Array.isArray(gate.check_ids) ? gate.check_ids : [];
  if (gate.usable === false) {
    return `<div class="alert danger">
      <strong>这个任务已失效</strong>：${esc(gate.invalidated_reason || '人工撤回了结论确认')}
      <div class="muted">${esc(gate.invalidated_at || '')} · 关联核查记录 ${ids.map((id) => '#' + id).join('、') || '—'}</div>
      <div class="muted">产物没被删除，但它已经不能当作"经人工确认的成稿"使用。回到「语义核查」重新确认结论后，从报告重新开一个视频任务。</div>
    </div>`;
  }
  return `<div class="alert ok">
    成稿闸门：本任务只用了 <strong>${ids.length}</strong> 条人工已确认的结论
    <span class="muted">（核查记录 ${ids.map((id) => '#' + id).join('、') || '—'}）</span>
  </div>`;
}

function pipelineCard(pipeline) {
  if (!pipeline || !pipeline.ok) return '';
  const data = pipeline.data;
  const status = data.task_status ? `<span class="tag info">任务 #${data.research_task_id} · ${esc(data.task_status)}</span>` : '';
  const blocked = data.blocked
    ? `<span class="tag warn">卡在 ${esc(data.blocked.task_type)}（${esc(data.blocked.failure_reason || '等人放行')}）</span>`
    : '';
  const counts = `<span class="tag">已确认 ${data.confirmed_checks} 条 · 待确认 ${data.pending_checks} 条</span>`;
  const action = data.can_resume
    ? `<button class="btn" data-continue="1">继续跑（先恢复，再推进 3 格）</button>`
    : '';
  return `<div class="card">
      <h2>下一步 <span class="tag ${data.can_resume ? 'ok' : ''}">${data.can_resume ? '可以继续' : '还差一步'}</span></h2>
      <div class="row">${status}${blocked}${counts}</div>
      <p>${esc(data.next_step || '')}</p>
      ${data.note ? `<div class="note">${esc(data.note)}</div>` : ''}
      <div class="row">${action}<a class="btn ghost" href="#/tasks/${data.research_task_id || ''}">去任务详情</a></div>
      <p class="muted">确认本身不会让系统自己花钱：要不要继续跑，由你按上面这个按钮决定。</p>
    </div>`;
}

function bindPipeline(box, reportId, pipeline) {
  const button = box.querySelector('button[data-continue]');
  if (!button || !pipeline || !pipeline.ok) return;
  button.onclick = async () => {
    const taskId = pipeline.data.research_task_id;
    button.disabled = true;
    button.textContent = '恢复中…';
    const resumed = await api(`/research-tasks/${taskId}/resume`, { method: 'POST', body: '{}' });
    if (!resumed.ok) {
      window.alert(`恢复失败：${(resumed.error || '').toString()}`);
      button.disabled = false;
      button.textContent = '继续跑（先恢复，再推进 3 格）';
      return;
    }
    button.textContent = '推进中（真实搜索/抓取/模型）…';
    const ran = await api(`/research-tasks/${taskId}/run`, {
      method: 'POST',
      body: JSON.stringify({ max_iterations: 3 }),
    });
    if (!ran.ok) {
      window.alert(`推进失败：${(ran.error || '').toString()}`);
    } else {
      const report = ran.data;
      window.alert(`推进完成：完成 ${(report.completed || []).length} 格，卡住 ${(report.blocked || []).length} 格`);
    }
    route();
  };
}

async function pageChecks(view) {
  const reports = await api('/research-reports?limit=50');
  const items = reports.ok ? reports.data.items : [];
  const preset = new URLSearchParams((window.location.hash.split('?')[1] || '')).get('report') || '';
  const options = items
    .map((item) => `<option value="${item.id}"${String(item.id) === String(preset) ? ' selected' : ''}>#${item.id} ${esc(item.title)}</option>`)
    .join('');
  view.innerHTML = `
    <div class="card">
      <h2>语义核查</h2>
      <div class="row">
        <label>选择报告<select id="checks-report"><option value="">（未选）</option>${options}</select></label>
        <button class="btn" id="checks-load">加载核查结果</button>
      </div>
      <p class="muted">判定由模型逐条给出，<strong>不等于人工确认</strong>；模型的「支持」永远不会自动变成「已核实」。
        只有人工复核后才会变成人工确认，两件事分开记。</p>
    </div>
    <div id="checks-box"><div class="empty">选一份报告查看逐条核查</div></div>`;

  const load = async () => {
    const reportId = document.getElementById('checks-report').value;
    const box = document.getElementById('checks-box');
    if (!reportId) { box.innerHTML = '<div class="empty">先选一份报告</div>'; return; }
    box.innerHTML = '<div class="note">读取核查结果…</div>';
    const [summary, checks, gate, pipeline] = await Promise.all([
      api(`/research-reports/${reportId}/checks/summary`),
      api(`/research-reports/${reportId}/checks?limit=100`),
      api(`/research-reports/${reportId}/draft-readiness`),
      api(`/research-reports/${reportId}/pipeline`),
    ]);
    if (!summary.ok) { box.innerHTML = failure(summary); return; }
    if (!checks.ok) { box.innerHTML = failure(checks); return; }
    const data = summary.data;
    const gateText = gate.ok
      ? (gate.data.allowed
        ? `<span class="tag ok">可成稿 · 已确认 ${gate.data.confirmed} 条</span>`
        : `<span class="tag warn">还不能成稿：${esc(gate.data.note || '')}</span>`)
      : '<span class="tag">闸门状态未知</span>';
    const nextCard = pipelineCard(pipeline);
    if (!data.total) {
      box.innerHTML = `<div class="card"><h2>还没有核查记录</h2>
        <div class="row">${gateText}</div>
        <p class="muted">这份报告还没跑到 <code>verify.claims</code>，或者当时没有可用模型（核查不会假装做过）。</p></div>`
        + nextCard;
      bindPipeline(box, reportId, pipeline);
      return;
    }
    const verdictCells = Object.entries(data.by_verdict || {})
      .map(([key, value]) => `<span class="tag">${esc(VERDICT_LABEL[key] || key)} ${value}</span>`)
      .join('');
    box.innerHTML = `
      <div class="card">
        <h2>${data.total} 条结论 · 模型花费 ${money(data.model_cost_cny)} · ${data.model_calls} 次调用</h2>
        <div class="row">${verdictCells}</div>
        <div class="row">${gateText}</div>
        <p class="muted">${esc(data.note || '')}</p>
        <p class="muted">口径：<strong>只有人工确认（confirmed）的结论可以进入成稿</strong>；
          模型判定只是意见，驳回与待复核的结论不会进稿。</p>
      </div>
      ${nextCard}
      <div class="card"><h2>逐条核查</h2>
        <div class="scroll"><table style="min-width:1240px">
          <thead><tr><th>#</th><th>结论</th><th>模型判定</th><th>理由</th><th>支持片段</th><th>反对片段</th><th>人工</th><th>操作</th></tr></thead>
          <tbody>
            ${checks.data.items.map((row) => `
              <tr data-check="${row.id}">
                <td class="num">${row.finding_index}</td>
                <td style="min-width:260px">${esc(row.claim)}<div class="muted">证据 ${esc((row.evidence_ids || []).join(', ') || '—')} · 来源 ${esc((row.source_ids || []).join(', ') || '—')}</div></td>
                <td style="min-width:220px">${verdictTag(row.verdict)}<div class="muted">${esc(row.reason || '')}</div></td>
                <td class="muted">${esc((row.issues || []).map((item) => item.code).join(', ') || '—')}</td>
                <td style="min-width:180px">${quoteList(row.supporting)}</td>
                <td style="min-width:180px">${quoteList(row.contradicting)}</td>
                <td>${humanTag(row.human_review_status)}${row.human_note ? `<div class="muted">${esc(row.human_note)}</div>` : ''}</td>
                <td class="actions">
                  <button class="btn ghost" data-review="confirmed" data-id="${row.id}">确认</button>
                  <button class="btn ghost" data-review="rejected" data-id="${row.id}">驳回</button>
                  <button class="btn ghost" data-review="needs_more_evidence" data-id="${row.id}">需补证据</button>
                </td>
              </tr>`).join('')}
          </tbody>
        </table></div>
      </div>`;

    const send = async (checkId, status) => {
      const note = window.prompt('写一句复核理由（会记进核查记录）', '') || '';
      const response = await api(`/research-reports/${reportId}/checks/${checkId}/review`, {
        method: 'POST',
        body: JSON.stringify({ status, note }),
      });
      if (!response.ok) { window.alert(failure(response).replace(/<[^>]+>/g, ' ').trim()); return; }
      await load();
    };
    box.querySelectorAll('button[data-review]').forEach((button) => {
      button.onclick = () => send(button.dataset.id, button.dataset.review);
    });
    bindPipeline(box, reportId, pipeline);
  };

  document.getElementById('checks-load').onclick = load;
  if (preset) load();
}

async function pageReports(view) {
  const result = await api('/research-reports?limit=50');
  const items = result.ok ? result.data.items : [];
  // 成稿闸门：只有人工确认过的结论才允许建视频任务。这里把每份报告的状态先取回来，
  // 免得用户点了按钮才知道被拦（本地工具，一次几十个小请求可以接受）。
  const gates = new Map();
  await Promise.all(items.map(async (row) => {
    const answer = await api(`/research-reports/${row.id}/draft-readiness`);
    if (answer.ok) gates.set(row.id, answer.data);
  }));
  const gateTag = (row) => {
    const state = gates.get(row.id);
    if (!state) return '<span class="tag">未知</span>';
    if (state.allowed) return `<span class="tag ok">可成稿 · 已确认 ${state.confirmed} 条</span>`;
    const label = state.reason === 'no_checks' ? '未做语义核查' : `待人工确认 ${state.total_checks} 条`;
    return `<span class="tag warn">${label}</span>`;
  };
  view.innerHTML = `
    <div class="card"><h2>研究报告</h2>
      <p class="muted">口径：语义核查的判定只是模型意见，<strong>必须人工确认（confirmed）之后才允许进入成稿</strong>。</p>
      ${result.ok ? table([
        { title: 'ID', key: 'id', num: true },
        { title: '标题', key: 'title' },
        { title: '核验状态', value: (row) => statusTag((row.verification_status || 'unverified').toLowerCase()), raw: true },
        { title: '成稿闸门', value: (row) => gateTag(row), raw: true },
        { title: '创建时间', value: (row) => when(row.created_at) },
        { title: '操作', value: (row) => `<a href="#/planning?report=${row.id}">用它做内容策划</a>
          · <a href="#/checks?report=${row.id}">看语义核查</a>
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
      const gate = gates.get(Number(reportId)) || {};
      if (!gate.allowed) {
        output.innerHTML = `<div class="card">
          <h2>还不能成稿</h2>
          <div class="note warn">${esc(gate.note || '这份报告还没有人工确认过的结论。')}</div>
          <p class="muted">先把结论逐条核对一遍：<a href="#/checks?report=${reportId}">打开语义核查页</a>，
            确认（confirmed）之后这里的按钮才会真的建任务。</p>
        </div>`;
        return;
      }
      output.innerHTML = '<div class="note">正在把研究材料搬进任务目录…</div>';
      const response = await api(`/research-reports/${reportId}/video-job`, { method: 'POST', body: '{}' });
      if (!response.ok) { output.innerHTML = failure(response); return; }
      const job = response.data;
      const outline = job.outline || {};
      const storyboardReady = Boolean(outline.ok);
      output.innerHTML = `<div class="card">
        <h2>视频任务目录已建好</h2>
        <p><code>${esc(job.path)}</code></p>
        <div class="row">
          <span class="tag info">阶段 ${esc(job.stage)}</span>
          <span class="tag">证据 ${job.evidence_count} 条</span>
          <span class="tag">来源 ${job.source_count} 条</span>
          ${storyboardReady
            ? `<span class="tag ok">分镜 ${outline.shot_count} 镜 · ${outline.total_seconds} 秒</span>`
            : `<span class="tag warn">分镜未排出：${esc(outline.reason || job.blocked_reason)}</span>`}
        </div>
        <div class="note ${job.blocked ? 'warn' : 'ok'}">${esc(job.next_step || '')}</div>
        <p class="muted">分镜是**纯规则**排的草稿：口播稿是原文摘引、逐镜标来源，没有任何模型结论；
          正式脚本仍需模型或人工写（<code>script/</code> 故意留空）。</p>
        <div class="row">
          <label><input type="checkbox" id="job-narration" /> 合成旁白（需要能连 TTS）</label>
          <button class="btn" id="job-render" ${storyboardReady ? '' : 'disabled'}>后台渲染成片</button>
        </div>
        <div id="job-render-out"></div>
        <a href="#/video">去视频预览页看已有成片</a>
      </div>`;

      const renderButton = document.getElementById('job-render');
      if (renderButton && storyboardReady) {
        renderButton.onclick = async () => {
          const narration = Boolean(document.getElementById('job-narration')?.checked);
          if (!window.confirm('真的开始渲染吗？这一步会占用本机 CPU 十几分钟。')) return;
          const box = document.getElementById('job-render-out');
          box.innerHTML = '<div class="note">已提交后台渲染，进度写到 <code>qc/render_run.json</code>…</div>';
          const started = await api(`/workspace/jobs/${job.job_id}/render`, {
            method: 'POST',
            body: JSON.stringify({ narration }),
          });
          if (!started.ok) { box.innerHTML = failure(started); return; }
          const poll = async () => {
            const state = await api(`/workspace/jobs/${job.job_id}/render`);
            if (!state.ok) { box.innerHTML = failure(state); return; }
            const data = state.data || {};
            if (data.status === 'running') {
              box.innerHTML = `<div class="note">渲染中（${esc(data.stage || 'starting')}）…</div>`;
              setTimeout(poll, 5000);
              return;
            }
            box.innerHTML = `<div class="note ${data.status === 'done' ? 'ok' : 'warn'}">
              状态 ${esc(data.status)} · 阶段 ${esc(data.stage || '')}
              ${data.report && data.report.output_path ? `<br/>产物 <code>${esc(data.report.output_path)}</code>` : ''}
              ${data.error ? `<br/>错误 ${esc(data.error)}` : ''}
            </div>`;
          };
          setTimeout(poll, 3000);
        };
      }
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

const SCRIPT_NEXT = {
  draft: [['送审', 'in_review']],
  in_review: [['通过', 'approved'], ['驳回', 'rejected']],
  rejected: [['回到草稿', 'draft']],
  approved: [['标记可出分镜', 'storyboard_ready']],
  storyboard_ready: [['标记已制作', 'produced']],
  produced: [['标记已发布', 'published']],
  published: [['归档', 'archived']],
  archived: [],
};

async function pageScripts(view) {
  const picker = await jobPicker(jobFromQuery());
  view.innerHTML = `
    <div class="card">
      <h2>脚本库（可审核的版本）</h2>
      <div class="row">
        <label>选题 ID<input id="sc-topic" type="number" min="1" placeholder="1"></label>
        <label>标题<input id="sc-title" placeholder="商业银行是怎么赚钱的"></label>
        <label>模板<select id="sc-template">
          <option value="financial_mechanism">financial_mechanism</option>
          <option value="industry_research">industry_research</option>
          <option value="case_documentary">case_documentary</option>
          <option value="football_explainer">football_explainer</option>
        </select></label>
      </div>
      <label>正文<textarea id="sc-body" rows="6" placeholder="口播稿正文；改内容请新开一版，不要原地覆盖"></textarea></label>
      <div class="row"><button class="btn" id="sc-create">新建一版脚本</button></div>
      <div id="sc-result"></div>
      <div id="sc-list"></div>
    </div>
    <div class="card">
      <h2>任务目录里的 script.md</h2>
      ${picker.html}
      <div id="script-box"></div>
    </div>`;

  const resultBox = document.getElementById('sc-result');
  const listBox = document.getElementById('sc-list');

  const refresh = async () => {
    const listed = await api('/scripts?limit=20');
    if (!listed.ok) { listBox.innerHTML = failure(listed); return; }
    const rows = listed.data.items || [];
    listBox.innerHTML = `
      <h3>最近 ${listed.data.total || 0} 个版本</h3>
      ${table([
        { title: 'ID', key: 'id', num: true },
        { title: '选题', key: 'topic_id', num: true },
        { title: '版本', key: 'version', num: true },
        { title: '状态', value: (row) => statusTag(row.status), raw: true },
        { title: '标题', value: (row) => esc(String(row.title || '').slice(0, 40)) },
        { title: '字数', key: 'word_count', num: true },
        { title: '批准人', value: (row) => esc(row.approved_by || '—') },
        { title: '操作', raw: true, value: (row) => {
          const moves = SCRIPT_NEXT[row.status] || [];
          const adaptButton = `<button class="btn ghost" data-adapt="${row.id}">一稿三版</button>`;
          return (moves.map(([label, next]) =>
            `<button class="btn ghost" data-script="${row.id}" data-next="${next}">${esc(label)}</button>`
          ).join(' ') || '<span class="muted">终态</span>') + ' ' + adaptButton;
        } },
      ], rows, '还没有脚本版本')}
      <div id="adapt-box"></div>`;
    listBox.querySelectorAll('button[data-script]').forEach((button) => {
      button.onclick = async () => {
        button.disabled = true;
        const moved = await api(`/scripts/${button.dataset.script}/status`, {
          method: 'PATCH',
          body: JSON.stringify({ status: button.dataset.next }),
        });
        resultBox.innerHTML = moved.ok
          ? `<div class="note ok">#${button.dataset.script} → ${esc(moved.data.status)}</div>`
          : failure(moved);
        await refresh();
      };
    });
    listBox.querySelectorAll('button[data-adapt]').forEach((button) => {
      button.onclick = async () => {
        button.disabled = true;
        const box = document.getElementById('adapt-box');
        box.innerHTML = '<div class="note">正在适配…</div>';
        const adapted = await api(`/scripts/${button.dataset.adapt}/adapt`, {
          method: 'POST',
          body: JSON.stringify({}),
        });
        button.disabled = false;
        if (!adapted.ok) { box.innerHTML = failure(adapted); return; }
        box.innerHTML = `<h3>一稿三版（脚本 #${adapted.data.script_id}）</h3>
          <div class="note">${esc(adapted.data.note || '')}</div>
          ${adapted.data.variants.map((item) => `
            <div class="alert ok" style="margin:10px 0">
              <div class="row" style="justify-content:space-between"><b>${esc(item.name)}</b>
                <button class="btn ghost" data-copy="${esc(item.platform)}">复制正文</button></div>
              <div class="note">标题：${esc(item.title)}</div>
              <div class="note">摘要：${esc(item.summary)}</div>
              <pre class="note" data-body="${esc(item.platform)}" style="white-space:pre-wrap">${esc(item.body)}</pre>
              <div class="note">标签：${item.hashtags.length ? esc(item.hashtags.map((t) => '#' + t).join(' ')) : '（无）'}</div>
              ${item.warnings.length ? `<ul class="note" style="margin:6px 0 0 16px">${item.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : '<div class="note ok">没有需要注意的地方</div>'}
            </div>`).join('')}`;
        box.querySelectorAll('button[data-copy]').forEach((copy) => {
          copy.onclick = async () => {
            const text = box.querySelector(`pre[data-body="${copy.dataset.copy}"]`)?.textContent || '';
            try {
              await navigator.clipboard.writeText(text);
              copy.textContent = '已复制';
            } catch (error) {
              copy.textContent = '复制失败，请手动选中';
            }
          };
        });
      };
    });
  };

  document.getElementById('sc-create').onclick = async (event) => {
    const button = event.currentTarget;
    const body = document.getElementById('sc-body').value;
    const payload = {
      topic_id: Number(document.getElementById('sc-topic').value || 0),
      title: document.getElementById('sc-title').value.trim(),
      body,
      template: document.getElementById('sc-template').value,
    };
    if (!payload.topic_id || !payload.title || !body.trim()) {
      resultBox.innerHTML = '<div class="note bad">选题 ID、标题、正文都要填。</div>';
      return;
    }
    button.disabled = true;
    const created = await api('/scripts', { method: 'POST', body: JSON.stringify(payload) });
    button.disabled = false;
    resultBox.innerHTML = created.ok
      ? `<div class="note ok">已建脚本 #${created.data.id}（v${created.data.version}，${esc(created.data.status)}）</div>`
      : failure(created);
    if (created.ok) await refresh();
  };

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
  await refresh();
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
    box.innerHTML = `${assetAuditCard(data.asset_audit)}<h3>素材清单</h3>${manifest}<h3>已批准来源登记</h3>${sources}`;
  };
  const select = document.getElementById('job-select');
  if (select) {
    select.onchange = () => { location.hash = `#/assets?job=${select.value}`; };
    load(select.value);
  }
}

const AUDIT_TONE = { error: 'bad', warning: 'warn', info: 'info' };

function assetAuditCard(audit) {
  if (!audit) return '';
  if (audit.error) {
    return `<div class="card"><h2>素材审计</h2><div class="empty">审计没跑成：${esc(audit.error)}</div></div>`;
  }
  const errors = audit.errors || 0;
  const warnings = audit.warnings || 0;
  const tone = errors ? 'bad' : (warnings ? 'warn' : 'ok');
  const label = errors ? `${errors} 个硬问题` : (warnings ? `${warnings} 条提醒` : '对得上');
  const interesting = (audit.findings || []).filter((item) => item.severity !== 'info');
  const infoLine = (audit.findings || []).find((item) => item.severity === 'info');
  const rows = interesting.length
    ? `<ul class="tight">${interesting.slice(0, 20).map((item) => `<li><span class="tag ${AUDIT_TONE[item.severity] || 'info'}">${esc(item.severity)}</span> ${item.asset_id ? `<code>${esc(item.asset_id)}</code>：` : ''}${esc(item.detail)}</li>`).join('')}</ul>`
    : `<p class="muted">${esc(infoLine ? infoLine.detail : '没有需要报告的问题。')}</p>`;
  return `<div class="card">
    <h2>素材审计 <span class="tag ${tone}">${esc(label)}</span></h2>
    <p class="muted">清单 ${audit.manifest_assets || 0} 条 · 分镜打算用 ${audit.storyboard_assets || 0} 个 · 时间轴实际用了 ${audit.timeline_assets || 0} 个 clip。
      这份审计只核对「清单说了什么」与「片子里实际有什么」是否一致，不判断素材好不好看。</p>
    ${rows}
  </div>`;
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
  const report = (detail.ok && detail.data.summary && detail.data.summary.report) || {};
  const calendar = await api('/calendar?limit=100');
  const scheduled = (calendar.ok ? calendar.data.items || [] : [])
    .find((row) => row.video_job_id === job.job_id) || null;
  const defaultWhen = tomorrowEvening();
  const suggestTitle = (report.title || job.job_id).slice(0, 40);

  view.innerHTML = `
    <div class="card">
      <h2>${esc(job.job_id)}</h2>
      ${gateBanner(job.draft_gate)}
      ${report.report_id ? `<p class="muted">来源报告 #${report.report_id}${report.research_task_id ? `（研究任务 #${report.research_task_id}）` : ''}：${esc((report.title || '').slice(0, 80))}</p>` : ''}
      <div class="row">
        ${withOutput.map((item) => `<a class="btn ${item.job_id === job.job_id ? '' : 'ghost'}" href="#/video?job=${item.job_id}">${esc(item.job_id)}</a>`).join('')}
      </div>
      ${file ? `<p class="muted">${esc(file.name)} · ${(file.byte_size / 1048576).toFixed(2)} MB</p>
      <video controls preload="metadata" src="${API}/workspace/jobs/${encodeURIComponent(job.job_id)}/file?path=${encodeURIComponent('output/' + file.name)}"></video>` : '<div class="empty">没有 MP4</div>'}
    </div>
    <div class="card"><h2>排进内容日历</h2>
      ${scheduled
        ? `<div class="note ok">已排期 #${scheduled.id} · ${esc(scheduled.platform)} · ${scheduled.scheduled_for ? esc(when(scheduled.scheduled_for)) : '未定时间'}
            · 就绪度 <strong>${esc((scheduled.readiness && scheduled.readiness.label) || '未知')}</strong>
            <a href="#/operations">去内容日历</a></div>`
        : `<div class="row">
            <input id="cal-job-title" value="${esc(suggestTitle)}" style="flex:1;min-width:260px">
            <select id="cal-job-platform">
              <option value="douyin">抖音</option>
              <option value="bilibili">B 站</option>
              <option value="xiaohongshu">小红书</option>
              <option value="wechat">公众号</option>
              <option value="video_account">视频号</option>
              <option value="youtube">YouTube</option>
              <option value="other">其他</option>
            </select>
            <input id="cal-job-when" type="datetime-local" value="${defaultWhen}">
            <button id="cal-job-create">排进日历</button>
          </div>
          <div class="note">排期只记"打算哪天发什么"，系统不会自己发布；这条排期会自动绑上本任务的成片与来源报告
            #${report.report_id || '（无）'}，之后在内容日历里能看到它的底片就绪度。</div>`
      }
      <div id="cal-job-feedback"></div>
    </div>
    <div class="card"><h2>质检报告</h2>
      ${qc ? `<pre>${esc(qc)}</pre>` : '<div class="empty">没有 qc/qc_report.md</div>'}
    </div>
    <div class="card"><h2>流水线</h2>
      <p class="muted">读取该任务的 storyboard/storyboard.csv 与素材清单，交给引擎产出时间轴；
        「渲染成片」会真的调用引擎渲染整条片子，一条 40 秒的竖屏要十几分钟，期间不要关页面。</p>
      <div class="row">
        <button class="btn" id="build-timeline">只生成时间轴</button>
        <button class="btn ghost" id="render-film">渲染成片（十几分钟）</button>
        <button class="btn ghost" id="content-qc">内容质检（数字/来源/字幕）</button>
      </div>
      <div class="row">
        <label class="muted"><input type="checkbox" id="with-narration">
          按分镜的口播稿合成旁白（真实 TTS，需要联网；不同音就明说没有旁白）</label>
      </div>
      <div id="pipeline-out"></div>
    </div>`;

  const runPipeline = async (render) => {
    const out = document.getElementById('pipeline-out');
    const narration = Boolean(document.getElementById('with-narration')?.checked);
    if (render && !window.confirm('真的开始渲染吗？这一步会占用本机 CPU 十几分钟。')) return;
    out.innerHTML = render
      ? '<div class="note">正在导入素材、渲染镜头并合成成片，请勿关闭页面…</div>'
      : '<div class="note">正在校验分镜并把素材交给引擎…</div>';
    const buttons = [document.getElementById('build-timeline'), document.getElementById('render-film')];
    buttons.forEach((button) => { if (button) button.disabled = true; });
    const result = await api(`/workspace/jobs/${job.job_id}/pipeline`, {
      method: 'POST',
      body: JSON.stringify({ render, narration, output_name: 'output/final.mp4' }),
    });
    buttons.forEach((button) => { if (button) button.disabled = false; });
    if (!result.ok) { out.innerHTML = failure(result); return; }
    const data = result.data;
    if (data.blocked) {
      out.innerHTML = `<div class="note warn">${esc(data.message || data.blocked_reason || '被阻塞')}</div>`;
      return;
    }
    const qc = data.qc && data.qc.checks
      ? `<pre>${esc(data.qc.checks.map((item) => `${item.ok ? '✓' : '✗'} ${item.name}: ${item.detail}`).join('\n'))}</pre>`
      : '';
    out.innerHTML = `<div class="note ok">${esc(data.stage)}：${data.storyboard_shots} 个镜头 · ${data.frames} 帧 · ${data.canvas.w}×${data.canvas.h}
        ${data.output_path ? `· 成片 ${esc(data.output_path)}（${(data.output_byte_size / 1048576).toFixed(2)} MB）` : `· 时间轴 ${esc(data.timeline_path || '')}`}</div>
      ${qc}
      <pre>${esc((data.engine_calls || []).join('\n'))}</pre>
      ${data.notes && data.notes.length ? `<p class="muted">${data.notes.map(esc).join(' ')}</p>` : ''}`;
  };

  document.getElementById('build-timeline').onclick = () => runPipeline(false);
  document.getElementById('render-film').onclick = () => runPipeline(true);

  const say = (target, ok, text) => {
    target.innerHTML = `<div class="note ${ok ? 'ok' : 'bad'}">${esc(text)}</div>`;
  };

  const calButton = document.getElementById('cal-job-create');
  if (calButton) {
    calButton.onclick = async () => {
      const box = document.getElementById('cal-job-feedback');
      const title = document.getElementById('cal-job-title').value.trim();
      if (title.length < 2) { say(box, false, '标题至少要两个字'); return; }
      const payload = {
        title,
        platform: document.getElementById('cal-job-platform').value,
        scheduled_for: document.getElementById('cal-job-when').value || null,
        status: 'rendered',
        video_job_id: job.job_id,
      };
      if (report.report_id) payload.research_report_id = report.report_id;
      calButton.disabled = true;
      let result = await api('/calendar', { method: 'POST', body: JSON.stringify(payload) });
      let reportNote = '';
      if (!result.ok && payload.research_report_id && result.status === 422) {
        // job.json 里的报告 id 属于"跑这条片子时那个库"。换了一个库（比如演示库、
        // 工作库）之后就查不到它——这时不该整条排期都失败，退回"不绑报告"再试一次，
        // 并把这件事如实写出来，别让人以为已经绑上了。
        const detail = (result.error && result.error.details) || {};
        if (String(detail.message || result.error.message || '').includes('research_report_id')) {
          delete payload.research_report_id;
          result = await api('/calendar', { method: 'POST', body: JSON.stringify(payload) });
          reportNote = `（报告 #${report.report_id} 在当前库里查不到，这条排期没绑报告）`;
        }
      }
      calButton.disabled = false;
      if (!result.ok) { say(box, false, `${result.status} ${result.error.message}`); return; }
      const entry = result.data;
      say(box, true, `已排期 #${entry.id}：${(entry.readiness && entry.readiness.label) || '就绪度未知'}`
        + `${entry.readiness && entry.readiness.next_step ? ' —— ' + entry.readiness.next_step : ''}${reportNote}`);
      setTimeout(() => route(), 900);
    };
  }

  const qcButton = document.getElementById('content-qc');
  if (qcButton) {
    qcButton.onclick = async () => {
      const out = document.getElementById('pipeline-out');
      qcButton.disabled = true;
      out.innerHTML = '<div class="note">正在核对画面上的数字、来源与字幕…</div>';
      const result = await api(`/workspace/jobs/${job.job_id}/content-qc`, { method: 'POST' });
      qcButton.disabled = false;
      if (!result.ok) { out.innerHTML = failure(result); return; }
      const data = result.data;
      if (data.blocked) {
        out.innerHTML = `<div class="note warn">${esc(data.message || data.reason || '没法核对')}</div>`;
        return;
      }
      const errors = (data.errors || []).map((item) => `✗ ${item.check}${item.shot_id ? '（' + item.shot_id + '）' : ''}: ${item.detail}`);
      const warnings = (data.warnings || []).map((item) => `! ${item.check}${item.shot_id ? '（' + item.shot_id + '）' : ''}: ${item.detail}`);
      out.innerHTML = `
        <div class="note ${data.ok ? 'ok' : 'warn'}">内容质检${data.ok ? '：没发现硬错' : '：有硬错'} ·
          ${data.stats.shots} 镜 · 画面数字 ${data.stats.distinct_numbers_on_screen} 个</div>
        ${errors.length ? `<pre>${esc(errors.join('\n'))}</pre>` : ''}
        ${warnings.length ? `<pre>${esc(warnings.join('\n'))}</pre>` : ''}
        <p class="muted">${esc(data.note || '')}</p>
        <p class="muted">结果已写入 ${esc((data.files || {}).markdown || '')}</p>`;
    };
  }
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
  const reviews = await api('/reviews?limit=20');
  const reviewRows = reviews.ok ? (reviews.data.items || []) : [];
  view.innerHTML = `
    <div class="card">
      <h2>记一次审核</h2>
      <div class="row">
        <label>对象类型<select id="rv-target-type">
          <option value="research_report">research_report</option>
          <option value="script">script</option>
          <option value="knowledge_item">knowledge_item</option>
        </select></label>
        <label>对象 ID<input id="rv-target-id" type="number" min="1" placeholder="1"></label>
        <label>结论<select id="rv-verdict">
          <option value="pass">pass（通过）</option>
          <option value="needs_revision">needs_revision（要改）</option>
          <option value="reject">reject（驳回）</option>
        </select></label>
        <label>审核人<input id="rv-name" placeholder="你的名字（必填，agent 不能自审）"></label>
        <label>阶段<select id="rv-stage">
          <option value="professional">professional</option>
          <option value="compliance">compliance</option>
          <option value="final">final</option>
        </select></label>
      </div>
      <label>意见<textarea id="rv-comment" rows="3" placeholder="选 needs_revision 时必须写清要改什么"></textarea></label>
      <div class="row"><button class="btn" id="rv-submit">记录审核结论</button></div>
      <div id="rv-result"></div>
      <p class="muted">结论会回写到对象上：研究报告 pass→verified / needs_revision→disputed /
      reject→refuted；脚本与知识条目只记流水，不改它们的业务状态——审核通过不等于可以发布。</p>
    </div>
    <div class="card">
      <h2>审核流水</h2>
      ${reviews.ok ? table([
        { title: 'ID', key: 'id', num: true },
        { title: '对象', value: (row) => `${esc(row.target_type)} #${row.target_id}` },
        { title: '版本', value: (row) => (row.target_version === null ? '—' : row.target_version) },
        { title: '结论', value: (row) => statusTag(row.verdict === 'pass' ? 'verified' : row.verdict === 'reject' ? 'failed' : 'paused'), raw: true },
        { title: '阶段', key: 'stage' },
        { title: '审核人', key: 'reviewer_name' },
        { title: '意见', value: (row) => esc(String(row.comment || '').slice(0, 60)) },
        { title: '时间', value: (row) => when(row.reviewed_at) },
      ], reviewRows, '还没有审核记录') : failure(reviews)}
    </div>
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

  document.getElementById('rv-submit').onclick = async (event) => {
    const button = event.currentTarget;
    const verdict = document.getElementById('rv-verdict').value;
    const comment = document.getElementById('rv-comment').value.trim();
    const payload = {
      target_type: document.getElementById('rv-target-type').value,
      target_id: Number(document.getElementById('rv-target-id').value || 0),
      verdict,
      reviewer_name: document.getElementById('rv-name').value.trim(),
      stage: document.getElementById('rv-stage').value,
      comment: comment || null,
    };
    const resultBox = document.getElementById('rv-result');
    if (!payload.target_id || !payload.reviewer_name) {
      resultBox.innerHTML = '<div class="note bad">对象 ID 和审核人都要填。</div>';
      return;
    }
    if (verdict === 'needs_revision' && !comment) {
      resultBox.innerHTML = '<div class="note bad">needs_revision 必须写清要改什么（comment 不能空）。</div>';
      return;
    }
    button.disabled = true;
    const created = await api('/reviews', { method: 'POST', body: JSON.stringify(payload) });
    button.disabled = false;
    resultBox.innerHTML = created.ok
      ? `<div class="note ok">已记录审核 #${created.data.id}（${esc(created.data.verdict)}）</div>`
      : failure(created);
    if (created.ok) await pageReview(document.getElementById('view'));
  };
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
    </div>
    <div class="card"><h2>运行历史（每天花了多少 / 跑几条 / 失败几次）</h2>
      <div class="row"><label>最近<select id="history-days">
        <option value="7">7 天</option>
        <option value="14" selected>14 天</option>
        <option value="30">30 天</option>
        <option value="90">90 天</option>
      </select></label></div>
      <div id="history-box"><div class="note">加载中…</div></div>
    </div>`;

  const historyBox = document.getElementById('history-box');
  const loadHistory = async (days) => {
    historyBox.innerHTML = '<div class="note">加载中…</div>';
    const history = await api(`/system/history?days=${days}`);
    if (!history.ok) { historyBox.innerHTML = failure(history); return; }
    historyBox.innerHTML = renderHistory(history.data);
  };
  document.getElementById('history-days').onchange = (event) => loadHistory(event.target.value);
  loadHistory(14);
}

/** 纯 SVG 折线：只画真实记录，0 就是 0，不插值、不补假数据。 */
function renderHistory(data) {
  const items = data.items || [];
  if (!items.length) return '<div class="note">这段时间没有记录。</div>';
  const width = 720;
  const height = 180;
  const pad = 28;
  const maxCost = Math.max(...items.map((row) => Number(row.cost_cny) || 0), 0.000001);
  const maxCalls = Math.max(...items.map((row) => Number(row.model_calls) || 0), 1);
  const x = (index) => pad + (items.length === 1 ? 0 : (index * (width - pad * 2)) / (items.length - 1));
  const y = (value, max) => height - pad - (Number(value) / max) * (height - pad * 2);
  const line = (pick, max) => items.map((row, index) => `${index ? 'L' : 'M'}${x(index).toFixed(1)},${y(pick(row), max).toFixed(1)}`).join(' ');
  const failBar = items.map((row, index) => {
    const count = Number(row.tasks_failed) || 0;
    if (!count) return '';
    const barHeight = Math.min(24, 6 + count * 6);
    return `<rect x="${(x(index) - 6).toFixed(1)}" y="${(height - pad - barHeight).toFixed(1)}" width="12" height="${barHeight}" fill="#ff2bd6" opacity="0.75"><title>${esc(row.day)} 失败 ${count}</title></rect>`;
  }).join('');
  const dots = items.map((row, index) => {
    const count = Number(row.model_calls) || 0;
    if (!count) return '';
    return `<circle cx="${x(index).toFixed(1)}" cy="${y(count, maxCalls).toFixed(1)}" r="2.5" fill="#35e0ff"><title>${esc(row.day)} 调用 ${count} 次 / ${row.tokens} tokens</title></circle>`;
  }).join('');
  return `
    <svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" role="img" aria-label="运行历史折线">
      <line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}" stroke="#2a3550" />
      <path d="${line((row) => row.cost_cny, maxCost)}" fill="none" stroke="#b6ff1f" stroke-width="2" />
      <path d="${line((row) => row.model_calls, maxCalls)}" fill="none" stroke="#35e0ff" stroke-width="1.5" stroke-dasharray="4 3" />
      ${failBar}${dots}
      <text x="${pad}" y="14" fill="#8f9bb0" font-size="11">绿线＝每天成本（上限 ${money(maxCost)}） 青虚线＝调用次数（上限 ${maxCalls}） 粉柱＝失败任务</text>
    </svg>
    <div class="row" style="gap:16px;flex-wrap:wrap">
      <span class="note">合计成本 ${money(data.total_cost_cny)}</span>
      <span class="note">调用 ${data.total_model_calls} 次</span>
      <span class="note">完成 ${data.total_tasks_completed} 条</span>
      <span class="note">失败 ${data.total_tasks_failed} 条</span>
    </div>
    <div class="note">${esc(data.note || '')}</div>
    ${table([
      { title: '日期', key: 'day' },
      { title: '调用', key: 'model_calls', num: true },
      { title: 'tokens', key: 'tokens', num: true },
      { title: '成本', value: (row) => money(row.cost_cny), num: true },
      { title: '新建任务', key: 'tasks_created', num: true },
      { title: '完成', key: 'tasks_completed', num: true },
      { title: '失败', key: 'tasks_failed', num: true },
      { title: '子任务失败', key: 'subtasks_failed', num: true },
      { title: '闸门', key: 'gates', num: true },
    ], items, '这段时间没有记录')}`;
}

const CALENDAR_STATUS_LABEL = {
  planned: '已排期',
  scripted: '已有脚本',
  rendered: '已渲染',
  exported: '已导出',
  archived: '已归档',
  cancelled: '已取消',
};

const PUBLISH_LABEL = {
  not_published: '未发布',
  published: '已发布',
  removed: '已撤下',
};

async function pageOperations(view) {
  const [list, analytics] = await Promise.all([
    api('/calendar?limit=100'),
    api('/calendar/analytics'),
  ]);
  if (!list.ok) { view.innerHTML = failure(list); return; }
  const rows = list.data.items || [];
  const stats = analytics.ok ? analytics.data : [];
  const today = new Date();
  const defaultWhen = tomorrowEvening();

  view.innerHTML = `
    <div class="grid">
      ${stats.length
        ? stats.map((row) => `
          <div class="card kpi"><span class="k">${esc(row.platform)}</span><span class="v">${row.entries}</span>
            <span class="muted">已发布 ${row.published}｜快照 ${row.snapshots}</span>
            <span class="muted">最新播放 ${row.latest_views}｜赞 ${row.latest_likes}</span>
            <span class="muted">${esc(row.note)}</span></div>`).join('')
        : '<div class="empty">还没有任何平台数据</div>'}
    </div>
    <div class="card"><h2>新建排期</h2>
      <div class="row">
        <input id="cal-title" placeholder="标题（至少两个字）" style="flex:1;min-width:240px">
        <select id="cal-platform">
          <option value="douyin">抖音</option>
          <option value="bilibili">B 站</option>
          <option value="xiaohongshu">小红书</option>
          <option value="wechat">公众号</option>
          <option value="video_account">视频号</option>
          <option value="youtube">YouTube</option>
          <option value="other">其他</option>
        </select>
        <input id="cal-when" type="datetime-local" value="${defaultWhen}">
        <input id="cal-job" placeholder="视频任务号（可留空）" style="width:200px">
        <button id="cal-create">加进日历</button>
      </div>
      <div class="note">系统不会自己发布：标「已发布」必须填链接，没发就是没发。
        填了视频任务号之后，「底片就绪度」会去 <code>content/jobs/&lt;任务号&gt;/</code> 里核对片子有没有渲染出来、
        它依据的结论还作不作数。</div>
      <div id="cal-feedback"></div>
    </div>
    <div class="card"><h2>排期（${rows.length} 条）</h2>
      ${table([
        { title: '#', key: 'id', num: true },
        { title: '标题', key: 'title' },
        { title: '平台', key: 'platform' },
        { title: '排期时间', value: (row) => row.scheduled_for ? when(row.scheduled_for) : '—' },
        { title: '进度', value: (row) => `<span class="tag">${esc(CALENDAR_STATUS_LABEL[row.status] || row.status)}</span>` , raw: true },
        { title: '底片就绪度', value: (row) => {
            const ready = row.readiness || {};
            const cls = ready.status === 'ready' ? 'ok'
              : (ready.status === 'draft_invalid' || ready.status === 'film_missing' ? 'bad' : '');
            const film = ready.film && ready.film.relative_path
              ? ` <span class="muted">${Math.round((ready.film.byte_size || 0) / 1024)} KB</span>` : '';
            return `<span class="tag ${cls}" title="${esc(ready.next_step || '')}">${esc(ready.label || '未知')}</span>${film}`;
          }, raw: true },
        { title: '发布', value: (row) => `<span class="tag ${row.publish_state === 'published' ? 'ok' : ''}">${esc(PUBLISH_LABEL[row.publish_state] || row.publish_state)}</span>`, raw: true },
        { title: '链接', value: (row) => row.published_url
            ? `<a href="${esc(row.published_url)}" target="_blank" rel="noreferrer">打开</a>` : '—', raw: true },
        { title: '操作', value: (row) => `
            <button data-advance="${row.id}" data-status="rendered">标已渲染</button>
            <button data-publish="${row.id}">标已发布</button>
            <button data-metric="${row.id}">回填数据</button>
            <button data-import="${row.id}">导入 CSV</button>
            <button data-export="${row.id}">导出</button>
            <button data-bind-job="${row.id}" data-job="${row.video_job_id || ''}">绑任务号</button>`, raw: true },
      ], rows, '还没有排期：先在上面加一条')}
      <div id="cal-row-feedback"></div>
    </div>`;

  const feedback = view.querySelector('#cal-row-feedback');
  const say = (target, ok, text) => {
    target.innerHTML = `<div class="note ${ok ? 'ok' : 'bad'}">${esc(text)}</div>`;
  };

  view.querySelector('#cal-create').addEventListener('click', async () => {
    const title = view.querySelector('#cal-title').value.trim();
    const jobId = view.querySelector('#cal-job').value.trim();
    const payload = {
      title,
      platform: view.querySelector('#cal-platform').value,
      scheduled_for: view.querySelector('#cal-when').value || null,
    };
    if (jobId) payload.video_job_id = jobId;
    const result = await api('/calendar', { method: 'POST', body: JSON.stringify(payload) });
    if (!result.ok) { say(view.querySelector('#cal-feedback'), false, `${result.status} ${result.error.message}`); return; }
    route();
  });

  view.querySelectorAll('[data-advance]').forEach((button) => {
    button.addEventListener('click', async () => {
      const id = button.getAttribute('data-advance');
      const result = await api(`/calendar/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ status: button.getAttribute('data-status') }),
      });
      if (!result.ok) { say(feedback, false, `${result.status} ${result.error.message}`); return; }
      route();
    });
  });

  view.querySelectorAll('[data-publish]').forEach((button) => {
    button.addEventListener('click', async () => {
      const id = button.getAttribute('data-publish');
      const url = window.prompt('发布链接（没发就别填，取消即可）');
      if (!url) return;
      const result = await api(`/calendar/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ publish_state: 'published', published_url: url }),
      });
      if (!result.ok) { say(feedback, false, `${result.status} ${result.error.message}`); return; }
      route();
    });
  });

  view.querySelectorAll('[data-metric]').forEach((button) => {
    button.addEventListener('click', async () => {
      const id = button.getAttribute('data-metric');
      const views = window.prompt('播放量（数字，取消则不填）');
      if (views === null) return;
      const likes = window.prompt('点赞数', '0') || '0';
      const payload = {
        captured_at: new Date().toISOString(),
        views: Number(views) || 0,
        likes: Number(likes) || 0,
      };
      const result = await api(`/calendar/${id}/metrics`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      if (!result.ok) { say(feedback, false, `${result.status} ${result.error.message}`); return; }
      say(feedback, true, '数据已回填（每次回填都是一个快照，不覆盖历史）');
    });
  });
  view.querySelectorAll('[data-import]').forEach((button) => {
    button.addEventListener('click', async () => {
      const id = button.getAttribute('data-import');
      const csvText = window.prompt(
        '把平台后台导出的 CSV 粘进来（第一行要有 captured_at/采集时间 与 views/播放量 之类的列）',
      );
      if (!csvText) return;
      const result = await api(`/calendar/${id}/metrics/import`, {
        method: 'POST',
        body: JSON.stringify({ csv_text: csvText }),
      });
      if (!result.ok) {
        const reason = result.error?.details?.reason ? `（${result.error.details.reason}）` : '';
        say(feedback, false, `${result.status} ${result.error.message}${reason}`);
        return;
      }
      const data = result.data;
      const bad = (data.error_detail || []).map((item) => `第 ${item.line} 行：${item.reason}`);
      const skipped = data.skipped || 0;
      say(
        feedback,
        data.errors === 0,
        `导入 ${data.imported} 行${skipped ? `，跳过 ${skipped} 行（时间点已有快照）` : ''}` +
          (bad.length ? `；${bad.length} 行有问题：${bad.slice(0, 3).join('；')}` : ''),
      );
    });
  });
  view.querySelectorAll('[data-export]').forEach((button) => {
    button.addEventListener('click', async () => {
      const id = button.getAttribute('data-export');
      const result = await api(`/calendar/${id}/export`, { method: 'POST', body: JSON.stringify({}) });
      if (!result.ok) {
        const reason = result.error?.details?.reason ? `（${result.error.details.reason}）` : '';
        say(feedback, false, `${result.status} ${result.error.message}${reason}`);
        return;
      }
      const data = result.data;
      const warns = (data.warnings || []).length;
      say(
        feedback,
        true,
        `已导出到 ${data.export_path}（${data.platforms.length} 个平台文件）` +
          (warns ? `；${warns} 条提醒` : '') +
          '。导出不等于发布：发布后请回来填链接。',
      );
      setTimeout(route, 1200);
    });
  });
  view.querySelectorAll('[data-bind-job]').forEach((button) => {
    button.addEventListener('click', async () => {
      const id = button.getAttribute('data-bind-job');
      const current = button.getAttribute('data-job') || '';
      const answer = window.prompt(
        '填视频任务号（content/jobs 下的目录名；留空表示解绑）：',
        current,
      );
      if (answer === null) return;
      const jobId = answer.trim();
      const result = await api(`/calendar/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ video_job_id: jobId ? jobId : null }),
      });
      if (!result.ok) {
        const reason = result.error?.details?.reason ? `（${result.error.details.reason}）` : '';
        say(feedback, false, `${result.status} ${result.error.message}${reason}`);
        return;
      }
      const ready = result.data.readiness || {};
      say(feedback, true, `已绑定：${ready.label || '未知'}。${ready.next_step || ''}`);
      setTimeout(route, 1200);
    });
  });
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
