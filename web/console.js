/* wb-history-console 控制台逻辑 */

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

let TASKS = [], PROMPTS = {}, CURRENT_ID = '';
let state = { q:'', st:'all', user:'', sort:'new', tools:true };
let activeId = null, timer = null;

/* ---------- 演示模式（?demo=1）----------
   截图/分享用：把所有真实文本换成占位内容，避免截屏泄露真实任务与路径。
   不影响正常使用，也不改动任何数据。 */
const DEMO = new URLSearchParams(location.search).has('demo');
const DEMO_TEXT = '这是一条用于界面展示的示例文本，真实内容已脱敏处理。';
const mText = s => DEMO ? DEMO_TEXT : s;
const mPath = s => DEMO ? '~/WorkBuddy/示例工作区/…' : s;
const mTitle = (s, i) => DEMO ? ('示例任务 ' + (i + 1)) : s;

if (DEMO) document.documentElement.classList.add('demo');

/* ---------- 数据 ---------- */

async function loadTasks(refresh){
  const r = await fetch('/api/tasks' + (refresh ? '?refresh=1' : ''));
  const d = await r.json();
  TASKS = d.tasks || [];
  CURRENT_ID = d.current_id || '';
  $('#sb-sub').textContent = `共 ${TASKS.length} 条任务${DEMO ? '' : ' · ' + (d.wb_dir || '')}`;
  buildUserSel();
  loadPrompts(refresh);
  render();
}

async function loadPrompts(refresh){
  try{
    const r = await fetch('/api/prompts' + (refresh ? '?refresh=1' : ''));
    PROMPTS = (await r.json()).prompts || {};
    render();
  }catch(e){ /* 搜索降级为只匹配标题 */ }
}

function buildUserSel(){
  const users = [...new Set(TASKS.map(t => t.user_id))];
  const curUser = TASKS.find(t => t.id === CURRENT_ID)?.user_id || '';
  const keep = state.user;
  $('#userSel').innerHTML =
    `<option value="">全部账号 · ${TASKS.length}</option>` +
    users.map((u, i) => {
      const n = TASKS.filter(t => t.user_id === u).length;
      const mark = (u === curUser && u !== keep) ? '（当前）' : '';
      const sel = (keep ? u === keep : u === curUser) ? 'selected' : '';
      const label = DEMO ? `账号 ${i + 1}` : `${esc(u.slice(0,8))}…`;
      return `<option value="${esc(u)}" ${sel}>${label} · ${n} 条 ${mark}</option>`;
    }).join('');
  if(!keep) state.user = curUser;
}

/* ---------- 列表 ---------- */

function hl(text, q){
  const t = esc(text || '');
  if(!q) return t;
  try{
    return t.replace(new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&') + ')','gi'), '<mark>$1</mark>');
  }catch(e){ return t; }
}

function match(t){
  if(state.user && t.user_id !== state.user) return false;
  if(state.st !== 'all' && t.status !== state.st) return false;
  if(!state.q) return true;
  const q = state.q.toLowerCase();
  const p = PROMPTS[t.id] || '';
  return (t.title + ' ' + p + ' ' + t.workspace + ' ' + t.cwd + ' ' + t.model).toLowerCase().includes(q);
}

function render(){
  let arr = TASKS.filter(match);
  if(state.sort === 'new') arr.sort((a,b) => b.created_at - a.created_at);
  else if(state.sort === 'old') arr.sort((a,b) => a.created_at - b.created_at);
  else arr.sort((a,b) => b.size_mb - a.size_mb);

  if(!arr.length){
    $('#list').innerHTML = `<div class="empty">没有匹配的任务</div>`;
    return;
  }

  $('#list').innerHTML = arr.map((t, i) => {
    const raw = (PROMPTS[t.id] || '').replace(/\s+/g,' ').trim();
    const p = DEMO ? DEMO_TEXT : raw;
    const cls = ['completed','terminated','error','working'].includes(t.status) ? t.status : 'other';
    const isCur = t.id === CURRENT_ID;
    return `<div class="task ${t.id===activeId?'active':''} ${isCur?'cur':''}" data-id="${t.id}">
      <div class="t-top">
        <span class="dot d-${cls}"></span>
        <span class="t-title">${DEMO ? mTitle(t.title, i) : hl(t.title, state.q)}</span>
        ${isCur ? '<span class="badge">当前</span>' : ''}
        <span class="t-when">${t.created_str.slice(5)}</span>
      </div>
      ${p ? `<div class="t-sub">${DEMO ? p : hl(p.slice(0,140), state.q)}</div>` : ''}
      <div class="t-meta">
        <span>📁 ${DEMO ? '示例工作区' : hl(t.workspace || '-', state.q)}</span>
        ${t.size_mb ? `<span>💾 ${t.size_mb} MB</span>` : ''}
        ${t.model ? `<span>🤖 ${esc(t.model)}</span>` : ''}
        <span>${esc(t.status)}</span>
      </div>
    </div>`;
  }).join('');
}

/* ---------- 详情 ---------- */

async function select(id){
  activeId = id;
  render();
  const t = TASKS.find(x => x.id === id);
  if(!t) return;

  $('#mhead').style.display = '';
  $('#m-title').textContent = (DEMO ? mTitle(t.title, 0) : t.title)
    + (id === CURRENT_ID ? '（当前会话）' : '');
  $('#m-meta').innerHTML =
    `${esc(t.created_str)} → ${esc(t.updated_str)} · ${esc(t.status)}` +
    (t.model ? ` · ${esc(t.model)}` : '') +
    `<br><span class="mono" style="color:var(--faint)">${esc(mPath(t.cwd || '无工作目录'))}</span>` +
    (t.jsonl ? `<br><span class="mono" style="color:var(--faint)">${esc(mPath(t.jsonl))}</span>` : '');
  $('#mbody').innerHTML = `<div class="spin">读取对话流水…</div>`;

  await loadMessages(id);
  startAutoRefresh();
}

async function loadMessages(id){
  const t = TASKS.find(x => x.id === id);
  const tools = state.tools ? '1' : '0';
  let d;
  try{
    const r = await fetch(`/api/session/${id}?tools=${tools}`);
    d = await r.json();
  }catch(e){
    $('#mbody').innerHTML = `<div class="empty">读取失败：${esc(e.message)}</div>`;
    return;
  }
  if(d.error){
    $('#mbody').innerHTML = `<div class="empty">${esc(d.error)}</div>`;
    return;
  }
  const msgs = d.messages || [];
  if(!msgs.length){
    $('#mbody').innerHTML = `<div class="empty">该任务没有可展示的对话内容</div>`;
    return;
  }

  const who = { user:'你', assistant:'AI', tool:'工具', tool_result:'结果', note:'备注' };
  $('#mbody').innerHTML = msgs.map(m => `
    <div class="msg ${m.kind}" data-line="${m.line}">
      <div class="msg-head">
        <span class="ln" title="点击定位到文件第 ${m.line} 行">L${m.line}</span>
        <span class="msg-who ${m.role}">${who[m.kind] || m.kind}${m.name ? ' · '+esc(DEMO ? '示例工具' : m.name) : ''}</span>
        <span class="msg-time">${esc(m.time || '')}</span>
      </div>
      <div class="msg-text">${esc(mText(m.text))}</div>
    </div>`).join('');
}

/* ---------- 定位 ---------- */

async function locate(action, path, line){
  try{
    const r = await fetch('/api/locate', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({action, path, line})
    });
    const d = await r.json();
    toast(d.ok ? (d.msg || '已执行') : ('失败：' + (d.error || '未知')));
  }catch(e){ toast('请求失败：' + e.message); }
}

function toast(msg, ms=1800){
  let el = $('#toast');
  if(!el){
    el = document.createElement('div');
    el.id = 'toast';
    el.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);' +
      'background:#1a1d23;color:#fff;padding:9px 18px;border-radius:8px;font-size:13px;' +
      'z-index:999;box-shadow:0 4px 18px rgba(0,0,0,.22)';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.display = '';
  clearTimeout(el._t);
  el._t = setTimeout(() => el.style.display = 'none', ms);
}

function copy(text){
  navigator.clipboard.writeText(text).then(() => toast('已复制：' + text))
    .catch(() => toast('复制失败，请手动选择'));
}

/* ---------- 模式切换 ---------- */

$('#modeFloat').addEventListener('click', () => {
  const w = window.open('/float', '_blank');
  if(!w) location.href = '/float';
});

/* ---------- 事件 ---------- */

let qTimer;
$('#q').addEventListener('input', e => {
  clearTimeout(qTimer);
  const v = e.target.value.trim();
  qTimer = setTimeout(() => { state.q = v; render(); }, 180);
});

document.querySelectorAll('.chip[data-st]').forEach(c => {
  c.addEventListener('click', () => {
    document.querySelectorAll('.chip[data-st]').forEach(x => x.classList.remove('on'));
    c.classList.add('on');
    state.st = c.dataset.st;
    render();
  });
});

$('#userSel').addEventListener('change', e => { state.user = e.target.value; render(); });
$('#sortSel').addEventListener('change', e => { state.sort = e.target.value; render(); });

$('#list').addEventListener('click', e => {
  const el = e.target.closest('.task');
  if(el) select(el.dataset.id);
});

// 点击行号 → 用编辑器打开到该行
$('#mbody').addEventListener('click', e => {
  const ln = e.target.closest('.ln');
  if(!ln) return;
  const t = TASKS.find(x => x.id === activeId);
  if(!t || !t.jsonl) return toast('该任务没有对话文件');
  locate('open_editor', t.jsonl, parseInt(ln.textContent.slice(1), 10));
});

$('#a-open-dir').addEventListener('click', () => {
  const t = TASKS.find(x => x.id === activeId);
  if(t && t.cwd) locate('open_dir', t.cwd);
  else toast('该任务没有工作目录');
});

$('#a-reveal').addEventListener('click', () => {
  const t = TASKS.find(x => x.id === activeId);
  if(t && t.jsonl) locate('reveal', t.jsonl);
  else toast('该任务没有对话文件');
});

$('#a-copy-path').addEventListener('click', () => {
  const t = TASKS.find(x => x.id === activeId);
  if(!t) return;
  copy(t.jsonl || t.cwd || '');
});

$('#a-jump').addEventListener('click', () => {
  const v = prompt('跳到第几行？（该任务对话文件的行号）');
  if(!v) return;
  const t = TASKS.find(x => x.id === activeId);
  if(t && t.jsonl) locate('open_editor', t.jsonl, parseInt(v, 10));
});

$('#a-reload').addEventListener('click', () => {
  if(activeId){
    fetch('/api/refresh').then(() => loadMessages(activeId));
    toast('已刷新');
  }
});

$('#btnRefresh').addEventListener('click', () => { loadTasks(true); toast('已重新扫描'); });

$('#showTools').addEventListener('change', e => {
  state.tools = e.target.checked;
  if(activeId) loadMessages(activeId);
});

/* ---------- 当前会话自动跟随 ---------- */

function startAutoRefresh(){
  clearInterval(timer);
  timer = setInterval(async () => {
    if(!activeId) return;
    const t = TASKS.find(x => x.id === activeId);
    if(!t || t.status !== 'working') return;   // 只跟踪进行中的会话
    const atBottom = $('#mbody').scrollHeight - $('#mbody').scrollTop - $('#mbody').clientHeight < 60;
    await loadMessages(activeId);
    if(atBottom) $('#mbody').scrollTop = $('#mbody').scrollHeight;
  }, 5000);
}

/* ---------- 启动 ---------- */

(async function init(){
  await loadTasks();
  if(CURRENT_ID){
    const t = TASKS.find(x => x.id === CURRENT_ID);
    if(t) select(CURRENT_ID);
  }
})();
