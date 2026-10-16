/* 悬浮面板逻辑：默认跟随「当前会话」，可切到历史任务检索 */

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

let TASKS = [], PROMPTS = {}, CURRENT_ID = '';
let tab = 'current', viewId = null, timer = null;
let pinned = false;              // 用户在查看某个具体任务时锁定，不跟随当前会话

/* ---------- 数据 ---------- */

async function boot(){
  const r = await fetch('/api/tasks');
  const d = await r.json();
  TASKS = d.tasks || [];
  CURRENT_ID = d.current_id || '';
  fetch('/api/prompts').then(x => x.json()).then(x => { PROMPTS = x.prompts || {}; if(tab==='history') renderHistory(); }).catch(()=>{});
  if(!pinned) viewId = CURRENT_ID;
  render();
  startAuto();
}

async function loadMsgs(id){
  const t = TASKS.find(x => x.id === id);
  $('#f-title').textContent = t ? t.title : '会话';
  $('#f-dot').className = 'dot d-' + (['completed','terminated','error','working'].includes(t?.status) ? t.status : 'other');

  let d;
  try{
    d = await (await fetch(`/api/session/${id}?tools=0`)).json();
  }catch(e){
    $('#f-body').innerHTML = `<div class="empty">读取失败</div>`;
    return;
  }
  if(d.error || !(d.messages || []).length){
    $('#f-body').innerHTML = `<div class="empty">${esc(d.error || '暂无对话内容')}</div>`;
    return;
  }

  const who = { user:'你', assistant:'AI', tool:'工具', tool_result:'结果' };
  $('#f-body').innerHTML = d.messages.map(m => `
    <div class="fl-msg ${m.kind}" data-line="${m.line}">
      <div class="h">
        <span class="ln" title="点击定位到第 ${m.line} 行">L${m.line}</span>
        <span>${who[m.kind] || m.kind}${m.name ? ' · '+esc(m.name) : ''}</span>
        <span style="margin-left:auto">${esc(m.time || '')}</span>
      </div>
      <div class="b">${esc(m.text)}</div>
    </div>`).join('');

  if(!pinned) $('#f-body').scrollTop = $('#f-body').scrollHeight;
}

function renderHistory(){
  const q = ($('#f-q')?.value || '').trim().toLowerCase();
  let arr = TASKS.slice().sort((a,b) => b.created_at - a.created_at);
  if(q){
    arr = arr.filter(t => (t.title + ' ' + (PROMPTS[t.id]||'') + ' ' + t.workspace).toLowerCase().includes(q));
  }
  if(!arr.length){ $('#f-body').innerHTML = `<div class="empty">没有匹配</div>`; return; }
  $('#f-body').innerHTML = arr.slice(0, 120).map(t => {
    const cls = ['completed','terminated','error','working'].includes(t.status) ? t.status : 'other';
    return `<div class="fl-task ${t.id===viewId?'active':''}" data-id="${t.id}">
      <div class="t1"><span class="dot d-${cls}"></span> ${esc(t.title)} ${t.id===CURRENT_ID?'<span class="badge">当前</span>':''}</div>
      <div class="t2">${esc(t.created_str.slice(5))} · ${esc(t.workspace || '-')}</div>
    </div>`;
  }).join('');
}

function render(){
  $('#f-back').style.display = pinned ? '' : 'none';
  $('#f-foot').style.display = (tab === 'history' && !pinned) ? '' : 'none';
  if(tab === 'history' && !pinned){ renderHistory(); }
  else if(viewId){ loadMsgs(viewId); }
  else { $('#f-body').innerHTML = `<div class="empty">没有进行中的会话</div>`; }
}

/* ---------- 事件 ---------- */

document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('on'));
    t.classList.add('on');
    tab = t.dataset.tab;
    pinned = false;
    if(tab === 'current') viewId = CURRENT_ID;
    render();
    if(tab === 'history') setTimeout(() => $('#f-q')?.focus(), 50);
  });
});

let qt;
$('#f-q').addEventListener('input', e => {
  clearTimeout(qt);
  qt = setTimeout(renderHistory, 200);
});

$('#f-body').addEventListener('click', e => {
  const ln = e.target.closest('.ln');
  if(ln){
    const t = TASKS.find(x => x.id === viewId);
    if(t?.jsonl) return locate('open_editor', t.jsonl, parseInt(ln.textContent.slice(1),10));
  }
  const task = e.target.closest('.fl-task');
  if(task){
    viewId = task.dataset.id;
    pinned = true;
    render();
  }
});

$('#f-back').addEventListener('click', () => {
  pinned = false;
  viewId = CURRENT_ID;
  render();
});

$('#f-reload').addEventListener('click', () => {
  fetch('/api/refresh').then(() => boot());
});

$('#f-locate').addEventListener('click', () => {
  const t = TASKS.find(x => x.id === viewId);
  if(t?.cwd) locate('open_dir', t.cwd);
});

// 切到完整控制台模式
$('#f-console').addEventListener('click', () => {
  const w = window.open('/', '_blank');
  if(!w) location.href = '/';
});

async function locate(action, path, line){
  const r = await fetch('/api/locate', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action, path, line})
  });
  const d = await r.json();
  toast(d.ok ? (d.msg || '已执行') : ('失败：' + (d.error||'')));
}

function toast(msg, ms=1600){
  let el = $('#toast');
  if(!el){
    el = document.createElement('div');
    el.id = 'toast';
    el.style.cssText = 'position:fixed;left:8px;right:8px;bottom:10px;background:#1a1d23;color:#fff;' +
      'padding:7px 12px;border-radius:7px;font-size:11.5px;z-index:999;text-align:center';
    document.body.appendChild(el);
  }
  el.textContent = msg; el.style.display = '';
  clearTimeout(el._t); el._t = setTimeout(() => el.style.display='none', ms);
}

/* ---------- 自动跟随当前会话 ---------- */

function startAuto(){
  clearInterval(timer);
  timer = setInterval(async () => {
    try{
      const d = await (await fetch('/api/current')).json();
      const id = d.task?.id || '';
      if(!id) return;
      if(id !== CURRENT_ID){
        CURRENT_ID = id;
        if(!pinned && tab === 'current'){
          viewId = id;
          render();
        }
      } else if(!pinned && tab === 'current' && d.message_count !== undefined){
        // 进行中的会话：有新内容就刷新
        const n = $('#f-body').querySelectorAll('.fl-msg').length;
        if(Math.abs(d.message_count - n) > 0){
          const atBottom = $('#f-body').scrollHeight - $('#f-body').scrollTop - $('#f-body').clientHeight < 60;
          await loadMsgs(id);
          if(atBottom) $('#f-body').scrollTop = $('#f-body').scrollHeight;
        }
      }
    }catch(e){ /* 服务未启动，忽略 */ }
  }, 4000);
}

boot();
