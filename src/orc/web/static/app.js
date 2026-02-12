// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const $=id=>document.getElementById(id);
const api=p=>fetch(p).then(r=>r.json());
const enc=encodeURIComponent;
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
function cap(s){return s[0].toUpperCase()+s.slice(1)}

// ---------------------------------------------------------------------------
// Toast notifications
// ---------------------------------------------------------------------------
function toast(msg, type='info') {
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  const txt = document.createElement('span');
  txt.textContent = msg;
  txt.style.flex = '1';
  el.appendChild(txt);
  const btn = document.createElement('button');
  btn.className = 'toast-close';
  btn.innerHTML = '&times;';
  btn.onclick = () => el.remove();
  el.appendChild(btn);
  $('toast-container').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 3000);
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let projects = {};
let selected = null;
let expanded = {};
let selVersion = 0;
let sidebarCollapsed = false;
let currentTab = 'overview';
let refreshTimer = null;
let roomsCache = [];

// Terminal state
let termRoom = null;
let xterm = null;
let fitAddon = null;
let ws = null;
let wsConnected = false;
let pollTimer = null;
let termMode = null; // 'ws' | 'poll' | null
let ansi = null;
let pollInputBuffer = '';
let pollInputTimer = null;

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
async function init() {
  try { ansi = new AnsiUp(); } catch(e) {}
  projects = await api('/api/projects');
  renderSidebar();
  const names = Object.keys(projects);
  const saved = localStorage.getItem('orc-selected-project');
  if (names.length && !selected) await sel(saved && names.includes(saved) ? saved : names[0]);
  startRefresh();
}

function startRefresh() {
  stopRefresh();
  refreshTimer = setInterval(refresh, 5000);
}

function stopRefresh() {
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------
function renderSidebar() {
  const el = $('project-list');
  el.innerHTML = '';
  const names = Object.keys(projects);
  if (!names.length) return;
  const hdr = document.createElement('div');
  hdr.className = 'group-header';
  hdr.innerHTML = '<span class="group-arrow' + (sidebarCollapsed ? ' collapsed' : '') + '">&#9660;</span> Projects' +
    '<button class="btn-icon" style="margin-left:auto;width:22px;height:22px;font-size:14px" onclick="event.stopPropagation();showAddProject()" title="Add project (p)">+</button>';
  hdr.onclick = () => { sidebarCollapsed = !sidebarCollapsed; renderSidebar(); };
  el.appendChild(hdr);
  if (sidebarCollapsed) return;
  for (const name of names) {
    const d = document.createElement('div');
    d.className = 'proj' + (selected === name ? ' active' : '');
    const span = document.createElement('span');
    span.textContent = name;
    d.appendChild(span);
    if (name !== 'orc') {
      const rm = document.createElement('button');
      rm.className = 'proj-rm';
      rm.textContent = '\u00d7';
      rm.title = 'Remove project';
      rm.onclick = (e) => { e.stopPropagation(); doRmProject(name); };
      d.appendChild(rm);
    }
    d.onclick = () => sel(name);
    el.appendChild(d);
  }
}

async function sel(name) {
  selected = name;
  expanded = {};
  selVersion++;
  localStorage.setItem('orc-selected-project', name);
  renderSidebar();
  $('topbar').classList.add('visible');
  $('project-title').textContent = name;
  switchTab('overview');
  await loadRooms();
}

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------
function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  if (tab === 'overview') {
    $('content').style.display = '';
    $('terminal-view').classList.remove('active');
    termDisconnect();
    startRefresh();
  } else {
    $('content').style.display = 'none';
    $('terminal-view').classList.add('active');
    stopRefresh();
    populateRoomSelect();
    if (!termRoom && roomsCache.length) {
      const alive = roomsCache.find(r => r.tmux);
      termRoom = alive ? alive.name : roomsCache[0].name;
    }
    if (termRoom) {
      $('room-select').value = termRoom;
      termConnect();
    }
  }
}

// ---------------------------------------------------------------------------
// Room loading & rendering
// ---------------------------------------------------------------------------
async function loadRooms() {
  if (!selected) return;
  const v = selVersion;
  const rooms = await api('/api/projects/' + enc(selected) + '/rooms');
  if (v !== selVersion) return;
  roomsCache = rooms;
  if (currentTab === 'overview') renderRooms(rooms);
}

function renderRooms(rooms) {
  $('placeholder').style.display = 'none';
  const el = $('rooms');
  el.style.display = 'grid';
  el.innerHTML = rooms.map(r => {
    const tc = r.tmux ? 'alive' : 'dead';
    const tl = r.tmux ? '\u25cf tmux' : '\u25cb tmux';
    const n = esc(r.name);
    const ne = n.replace(/'/g, "\\'");
    const isMain = r.name === '@main';
    return '<div class="card" tabindex="0" role="listitem" data-room="' + n + '">' +
      '<div class="card-head">' +
        '<span class="card-name">' + n + '</span>' +
        '<span class="status-badge ss-' + r.status + '">' + esc(r.status) + '</span>' +
      '</div>' +
      '<div class="meta">' +
        '<span>' + esc(r.role) + '</span>' +
        (r.model ? '<span class="model-badge" title="Model">' + esc(r.model) + '</span>' : '') +
        '<span class="tmux ' + tc + '" title="tmux session">' + tl + '</span>' +
        '<span title="Inbox messages">\u2709 ' + r.inbox_count + (r.unread_count ? ' (' + r.unread_count + ' new)' : '') + '</span>' +
        '<span title="Molecules">\u25c6 ' + r.molecule_count + '</span>' +
      '</div>' +
      '<div class="card-actions">' +
        '<div class="btn-group">' +
          (r.tmux
            ? '<button class="btn btn-sm btn-danger" onclick="doKill(\'' + ne + '\')" title="Kill tmux session">Kill</button>' +
              '<button class="btn btn-sm" onclick="openTerminal(\'' + ne + '\')" title="Open terminal (t)">Terminal</button>'
            : '<button class="btn btn-sm btn-green" onclick="doLaunch(\'' + ne + '\',\'' + esc(r.role) + '\')" title="Launch tmux session">Launch</button>'
          ) +
        '</div>' +
        '<div class="btn-sep"></div>' +
        '<div class="btn-group">' +
          '<button class="btn btn-sm" onclick="showTell(\'' + ne + '\')" title="Tell (send to session)">Tell</button>' +
          '<button class="btn btn-sm" onclick="showSend(\'' + ne + '\')" title="Send inbox message (m)">Send</button>' +
        '</div>' +
        (isMain ? '' :
          '<div class="btn-sep"></div>' +
          '<div class="btn-group">' +
            '<button class="btn btn-sm btn-danger" onclick="doRmRoom(\'' + ne + '\')" title="Remove room">Remove</button>' +
          '</div>'
        ) +
      '</div>' +
      '<button class="toggle" data-room="' + n + '" data-section="inbox">\u25bc Inbox</button>' +
      '<div class="panel open" data-panel="' + n + '|inbox"></div>' +
      '<button class="toggle" data-room="' + n + '" data-section="molecules">\u25bc Molecules</button>' +
      '<div class="panel open" data-panel="' + n + '|molecules"></div>' +
    '</div>';
  }).join('');

  for (const r of rooms) {
    const n = esc(r.name);
    expanded[n + '|inbox'] = true;
    expanded[n + '|molecules'] = true;
    const ip = document.querySelector('[data-panel="' + CSS.escape(n + '|inbox') + '"]');
    const mp = document.querySelector('[data-panel="' + CSS.escape(n + '|molecules') + '"]');
    if (ip) loadPanel(n, 'inbox', ip);
    if (mp) loadPanel(n, 'molecules', mp);
  }
}


// ---------------------------------------------------------------------------
// Panel toggle & loading
// ---------------------------------------------------------------------------
document.addEventListener('click', async e => {
  const btn = e.target.closest('.toggle');
  if (!btn) return;
  const room = btn.dataset.room;
  const section = btn.dataset.section;
  const key = room + '|' + section;
  const panel = document.querySelector('[data-panel="' + CSS.escape(key) + '"]');
  if (!panel) return;

  if (panel.classList.contains('open')) {
    panel.classList.remove('open');
    btn.textContent = '\u25b6 ' + cap(section);
    delete expanded[key];
    return;
  }

  await loadPanel(room, section, panel);
  panel.classList.add('open');
  btn.textContent = '\u25bc ' + cap(section);
  expanded[key] = true;
});

async function loadPanel(room, section, panel) {
  if (section === 'inbox') {
    const msgs = await api('/api/projects/' + enc(selected) + '/rooms/' + enc(room) + '/inbox');
    if (!msgs.length) {
      panel.innerHTML = '<div class="empty">No messages</div>';
    } else {
      panel.innerHTML = msgs.map(m =>
        '<div class="msg' + (m.read ? '' : ' unread') + '">' +
          '<span class="msg-from">' + esc(m.from || '?') + '</span>' +
          '<span class="msg-ts">' + esc(m.ts || '') + '</span>' +
          '<div class="msg-body">' + esc(m.message || '') + '</div>' +
        '</div>'
      ).join('');
    }
  } else {
    const mols = await api('/api/projects/' + enc(selected) + '/rooms/' + enc(room) + '/molecules');
    if (!mols.length) {
      panel.innerHTML = '<div class="empty">No molecules</div>';
    } else {
      panel.innerHTML = mols.map(mol => {
        let h = '<div class="mol-title">' + esc(mol.title || mol.id || 'Untitled');
        if (mol.status) h += ' <span class="badge s-' + (mol.status === 'in_progress' ? 'working' : mol.status) + '">' + mol.status + '</span>';
        h += '</div>';
        if (mol.atoms) {
          h += mol.atoms.map(a =>
            '<div class="atom">' +
              '<span class="atom-dot ' + (a.status || 'todo') + '"></span>' +
              '<span>' + esc(a.title || a.id) + '</span>' +
              '<span class="badge s-' + (a.status === 'in_progress' ? 'working' : (a.status || 'unknown')) + '">' +
                (a.status || '?') +
              '</span>' +
            '</div>'
          ).join('');
        }
        return h;
      }).join('<hr class="mol-sep">');
    }
  }
}

// ---------------------------------------------------------------------------
// Auto-refresh
// ---------------------------------------------------------------------------
async function refresh() {
  if (currentTab !== 'overview') return;
  const dot = $('dot');
  dot.classList.add('loading');
  try {
    projects = await api('/api/projects');
    renderSidebar();
    if (selected && projects[selected]) {
      await loadRooms();
      for (const key of Object.keys(expanded)) {
        const i = key.lastIndexOf('|');
        const room = key.slice(0, i);
        const section = key.slice(i + 1);
        const panel = document.querySelector('[data-panel="' + CSS.escape(key) + '"]');
        const btn = document.querySelector('.toggle[data-room="' + CSS.escape(room) + '"][data-section="' + CSS.escape(section) + '"]');
        if (panel && btn) {
          await loadPanel(room, section, panel);
          panel.classList.add('open');
          btn.textContent = '\u25bc ' + cap(section);
        }
      }
    }
  } catch(e) {}
  dot.classList.remove('loading');
}

// ---------------------------------------------------------------------------
// Room actions
// ---------------------------------------------------------------------------
async function doLaunch(room, role) {
  toast('Launching ' + room + '...', 'info');
  try {
    const res = await fetch('/api/projects/' + enc(selected) + '/rooms/' + enc(room) + '/attach', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({role: role})
    });
    const data = await res.json();
    if (data.error) toast('Error: ' + data.error, 'error');
    else toast(room + ' launched', 'success');
  } catch(e) { toast('Launch failed', 'error'); }
  await loadRooms();
}

async function doKill(room) {
  if (!confirm('Kill tmux session for "' + room + '"?')) return;
  try {
    const res = await fetch('/api/projects/' + enc(selected) + '/rooms/' + enc(room) + '/kill', {
      method: 'POST'
    });
    const data = await res.json();
    if (data.error) toast('Error: ' + data.error, 'error');
    else toast(room + ' killed', 'success');
  } catch(e) { toast('Kill failed', 'error'); }
  await loadRooms();
}


async function doClean() {
  try {
    const res = await fetch('/api/projects/' + enc(selected) + '/clean', {
      method: 'POST'
    });
    const data = await res.json();
    if (data.error) toast('Error: ' + data.error, 'error');
    else {
      const parts = [];
      if (data.messages !== undefined) parts.push(data.messages + ' messages');
      if (data.molecules !== undefined) parts.push(data.molecules + ' molecules');
      toast('Cleaned: ' + (parts.length ? parts.join(', ') : 'done'), 'success');
    }
  } catch(e) { toast('Clean failed', 'error'); }
  await loadRooms();
}

async function doShutdown() {
  if (!confirm('Stop the sandbox?\n\nThis will stop all running agents and the web UI.')) return;
  try {
    await fetch('/api/shutdown', {method: 'POST'});
    toast('Sandbox stopping...', 'success');
  } catch(e) { /* expected — server dies */ }
}

// ---------------------------------------------------------------------------
// Add Room
// ---------------------------------------------------------------------------
function showAddRoom() { $('modal-add').classList.add('open'); $('add-room-name').value = ''; $('add-room-model').value = ''; $('add-room-message').value = ''; $('add-room-name').focus(); }
function hideAddRoom() { $('modal-add').classList.remove('open'); }

async function doAddRoom(andLaunch) {
  const name = $('add-room-name').value.trim();
  const role = $('add-room-role').value;
  const model = $('add-room-model').value;
  const message = $('add-room-message').value.trim();
  if (!name) return;
  hideAddRoom();
  try {
    const addBody = {room_name: name, role: role};
    if (model) addBody.model = model;
    const res = await fetch('/api/projects/' + enc(selected) + '/rooms/add', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(addBody)
    });
    const data = await res.json();
    if (data.error) { toast('Error: ' + data.error, 'error'); return; }
    toast('Room ' + name + ' created', 'success');
    if (andLaunch) {
      const body = {role: role};
      if (model) body.model = model;
      if (message) body.message = message;
      await fetch('/api/projects/' + enc(selected) + '/rooms/' + enc(name) + '/attach', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      toast(name + ' launched', 'success');
    }
  } catch(e) { toast('Add room failed', 'error'); }
  await loadRooms();
}

// ---------------------------------------------------------------------------
// Add Project
// ---------------------------------------------------------------------------
function showAddProject() { $('modal-add-project').classList.add('open'); $('add-project-url').value = ''; $('add-project-name').value = ''; $('add-project-url').focus(); }
function hideAddProject() { $('modal-add-project').classList.remove('open'); }

async function doAddProject() {
  const url = $('add-project-url').value.trim();
  const name = $('add-project-name').value.trim();
  if (!url) return;
  hideAddProject();
  toast('Cloning repository\u2026', 'info');
  try {
    const res = await fetch('/api/projects/add', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url, name: name || undefined})
    });
    const data = await res.json();
    if (data.error) { toast('Error: ' + data.error, 'error'); return; }
    toast('Project ' + data.name + ' cloned', 'success');
    projects = await api('/api/projects');
    renderSidebar();
    await sel(data.name);
  } catch(e) { toast('Clone failed', 'error'); }
}

async function doRmProject(name) {
  if (!confirm('Remove project "' + name + '"?\n\nThis deletes the cloned repo from orc/projects/.')) return;
  try {
    const res = await fetch('/api/projects/' + encodeURIComponent(name) + '/rm', {
      method: 'POST'
    });
    const data = await res.json();
    if (data.error) { toast('Error: ' + data.error, 'error'); return; }
    toast('Project ' + name + ' removed', 'success');
    projects = await api('/api/projects');
    if (selected === name) { selected = null; const names = Object.keys(projects); if (names.length) await sel(names[0]); }
    renderSidebar();
  } catch(e) { toast('Remove project failed', 'error'); }
}

// ---------------------------------------------------------------------------
// Tell Modal
// ---------------------------------------------------------------------------
let tellTarget = null;
function showTell(room) { tellTarget = room; $('tell-room-name').textContent = room; $('tell-message').value = ''; $('modal-tell').classList.add('open'); $('tell-message').focus(); }
function hideTell() { $('modal-tell').classList.remove('open'); }

async function doTell() {
  const msg = $('tell-message').value.trim();
  if (!msg || !tellTarget) return;
  hideTell();
  try {
    await fetch('/api/projects/' + enc(selected) + '/rooms/' + enc(tellTarget) + '/tell', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg})
    });
    toast('Sent to ' + tellTarget, 'success');
  } catch(e) { toast('Tell failed', 'error'); }
}

// ---------------------------------------------------------------------------
// Send Inbox Message Modal
// ---------------------------------------------------------------------------
let sendTarget = null;
function showSend(room) { sendTarget = room; $('send-room-name').textContent = room; $('send-message').value = ''; $('modal-send').classList.add('open'); $('send-message').focus(); }
function hideSend() { $('modal-send').classList.remove('open'); }

async function doSend() {
  const msg = $('send-message').value.trim();
  const from = $('send-from').value.trim() || '@main';
  if (!msg || !sendTarget) return;
  hideSend();
  try {
    await fetch('/api/projects/' + enc(selected) + '/rooms/' + enc(sendTarget) + '/send', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg, from: from})
    });
    toast('Message sent to ' + sendTarget, 'success');
  } catch(e) { toast('Send failed', 'error'); }
  await loadRooms();
}

// ---------------------------------------------------------------------------
// Remove room
// ---------------------------------------------------------------------------
async function doRmRoom(name) {
  if (!confirm('Remove room "' + name + '"? This will delete the worktree and kill its tmux session.')) return;
  await fetch('/api/projects/' + enc(selected) + '/rooms/' + enc(name) + '/rm', {method: 'POST'});
  toast(name + ' removed', 'success');
  await loadRooms();
}

// ---------------------------------------------------------------------------
// Terminal view
// ---------------------------------------------------------------------------
function populateRoomSelect() {
  const sel = $('room-select');
  sel.innerHTML = roomsCache.map(r =>
    '<option value="' + esc(r.name) + '"' + (r.name === termRoom ? ' selected' : '') + '>' +
      esc(r.name) + (r.tmux ? ' \u25cf' : '') +
    '</option>'
  ).join('');
}

function openTerminal(room) {
  termRoom = room;
  switchTab('terminal');
}

function termSelectRoom() {
  termRoom = $('room-select').value;
  termConnect();
}

function termConnect() {
  termDisconnect();
  updateConnBadge(null);
  tryWebSocket();
}

function termDisconnect() {
  if (ws) { ws.onclose = null; ws.close(); ws = null; }
  wsConnected = false;
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  termMode = null;
  if (xterm) { xterm.dispose(); xterm = null; fitAddon = null; }
  $('xterm-container').innerHTML = '';
  $('xterm-container').style.display = '';
  $('terminal-fallback').classList.remove('active');
  $('terminal-fallback').innerHTML = '';
}

function updateConnBadge(mode) {
  const el = $('conn-indicator');
  el.className = 'conn-badge';
  if (mode === 'ws') { el.textContent = 'WS'; el.classList.add('conn-ws'); }
  else if (mode === 'poll') { el.textContent = 'Polling'; el.classList.add('conn-poll'); }
  else { el.textContent = '--'; el.classList.add('conn-off'); }
}

// ---------------------------------------------------------------------------
// WebSocket terminal (PTY-bridged — real terminal stream)
// ---------------------------------------------------------------------------
function tryWebSocket() {
  if (!selected || !termRoom) return;
  const wsPort = parseInt(window.location.port, 10) + 1;
  const wsUrl = 'ws://' + window.location.hostname + ':' + wsPort + '/terminal/' + enc(selected) + '/' + enc(termRoom);

  initXterm();

  ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => {
    wsConnected = true;
    termMode = 'ws';
    updateConnBadge('ws');
    sendResize();
    if (xterm) xterm.focus();
  };
  ws.onmessage = (ev) => {
    if (!xterm) return;
    if (ev.data instanceof ArrayBuffer) {
      xterm.write(new Uint8Array(ev.data));
    } else {
      xterm.write(ev.data);
    }
  };
  ws.onclose = () => {
    wsConnected = false;
    if (termMode === 'ws') {
      setTimeout(() => {
        if (currentTab === 'terminal' && termRoom) {
          startPollingFallback();
        }
      }, 2000);
    }
  };
  ws.onerror = () => {
    wsConnected = false;
    if (ws) { ws.onclose = null; ws.close(); ws = null; }
    startPollingFallback();
  };
}

function sendResize() {
  if (!ws || !wsConnected || !fitAddon) return;
  try {
    fitAddon.fit();
    const dims = fitAddon.proposeDimensions();
    if (dims) {
      ws.send(JSON.stringify({type: 'resize', rows: dims.rows, cols: dims.cols}));
    }
  } catch(e) {}
}

function initXterm() {
  if (xterm) { xterm.dispose(); xterm = null; }
  $('xterm-container').innerHTML = '';
  $('xterm-container').style.display = '';
  $('terminal-fallback').classList.remove('active');

  xterm = new window.Terminal({
    theme: {
      background: '#0d1117',
      foreground: '#e6edf3',
      cursor: '#58a6ff',
      selectionBackground: 'rgba(88,166,255,0.3)',
      black: '#0d1117',
      red: '#f85149',
      green: '#3fb950',
      yellow: '#d29922',
      blue: '#58a6ff',
      magenta: '#bc8cff',
      cyan: '#76e3ea',
      white: '#e6edf3',
    },
    fontSize: 14,
    fontFamily: "'Menlo', 'Monaco', 'Courier New', monospace",
    cursorBlink: true,
    scrollback: 5000,
  });

  fitAddon = new window.FitAddon.FitAddon();
  xterm.loadAddon(fitAddon);
  xterm.open($('xterm-container'));
  fitAddon.fit();

  // Send raw terminal data (includes escape sequences for arrows, ctrl+c, etc.)
  xterm.onData(data => {
    if (ws && wsConnected) {
      ws.send(data);
    } else if (selected && termRoom) {
      // Polling fallback: batch keystrokes and send via HTTP
      pollInputBuffer += data;
      if (pollInputTimer) clearTimeout(pollInputTimer);
      pollInputTimer = setTimeout(flushPollInput, 16);
    }
  });

  window.addEventListener('resize', () => {
    if (fitAddon) fitAddon.fit();
    sendResize();
  });
}

// ---------------------------------------------------------------------------
// Polling input flush (batches keystrokes for HTTP fallback)
// ---------------------------------------------------------------------------
function flushPollInput() {
  if (!pollInputBuffer || !selected || !termRoom) return;
  const data = pollInputBuffer;
  pollInputBuffer = '';
  pollInputTimer = null;
  fetch('/api/projects/' + enc(selected) + '/rooms/' + enc(termRoom) + '/terminal/input', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({data: data})
  }).catch(() => {});
}

// ---------------------------------------------------------------------------
// Polling fallback (uses xterm.js for proper ANSI rendering)
// ---------------------------------------------------------------------------
let lastPollContent = '';

function startPollingFallback() {
  if (pollTimer) return;
  termMode = 'poll';
  updateConnBadge('poll');

  // Keep xterm.js for rendering — it handles ANSI natively
  if (!xterm) initXterm();
  $('xterm-container').style.display = '';
  $('terminal-fallback').classList.remove('active');

  lastPollContent = '';
  pollTerminal();
  pollTimer = setInterval(pollTerminal, 300);
  if (xterm) xterm.focus();
}

async function pollTerminal() {
  if (!selected || !termRoom) return;
  try {
    const res = await fetch('/api/projects/' + enc(selected) + '/rooms/' + enc(termRoom) + '/terminal');
    const data = await res.json();
    const content = data.content || '';
    if (content && content !== lastPollContent) {
      lastPollContent = content;
      xterm.reset();
      xterm.write(content);
    }
  } catch(e) {}
}

// ---------------------------------------------------------------------------
// Close modals on backdrop click
// ---------------------------------------------------------------------------
document.querySelectorAll('.modal-bg').forEach(bg => {
  bg.addEventListener('click', e => {
    if (e.target === bg) bg.classList.remove('open');
  });
});

// ---------------------------------------------------------------------------
// Keyboard navigation
// ---------------------------------------------------------------------------
let focusedCardIndex = -1;

function getCards() {
  return Array.from(document.querySelectorAll('#rooms .card[tabindex]'));
}

function focusCard(idx) {
  const cards = getCards();
  if (!cards.length) return;
  focusedCardIndex = Math.max(0, Math.min(idx, cards.length - 1));
  cards[focusedCardIndex].focus();
}

function isModalOpen() {
  return !!document.querySelector('.modal-bg.open') || $('keyboard-help').classList.contains('open');
}

function isInputFocused() {
  const el = document.activeElement;
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

function closeAllModals() {
  document.querySelectorAll('.modal-bg.open').forEach(m => m.classList.remove('open'));
  $('keyboard-help').classList.remove('open');
}

function toggleHelp() {
  $('keyboard-help').classList.toggle('open');
}

// Track which element opened a modal so we can restore focus
let modalTrigger = null;

// Wrap modal open functions to track trigger
const _origShowAddProject = showAddProject;
showAddProject = function() { modalTrigger = document.activeElement; _origShowAddProject(); };
const _origShowAddRoom = showAddRoom;
showAddRoom = function() { modalTrigger = document.activeElement; _origShowAddRoom(); };
const _origShowTell = showTell;
showTell = function(room) { modalTrigger = document.activeElement; _origShowTell(room); };
const _origShowSend = showSend;
showSend = function(room) { modalTrigger = document.activeElement; _origShowSend(room); };

// Wrap modal close functions to restore focus
const _origHideAddProject = hideAddProject;
hideAddProject = function() { _origHideAddProject(); if (modalTrigger) { modalTrigger.focus(); modalTrigger = null; } };
const _origHideAddRoom = hideAddRoom;
hideAddRoom = function() { _origHideAddRoom(); if (modalTrigger) { modalTrigger.focus(); modalTrigger = null; } };
const _origHideTell = hideTell;
hideTell = function() { _origHideTell(); if (modalTrigger) { modalTrigger.focus(); modalTrigger = null; } };
const _origHideSend = hideSend;
hideSend = function() { _origHideSend(); if (modalTrigger) { modalTrigger.focus(); modalTrigger = null; } };

// Focus trap inside modals
document.addEventListener('keydown', e => {
  if (e.key === 'Tab') {
    const openModal = document.querySelector('.modal-bg.open .modal');
    if (!openModal) return;
    const focusable = openModal.querySelectorAll('input,select,textarea,button,[tabindex]:not([tabindex="-1"])');
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey) {
      if (document.activeElement === first) { e.preventDefault(); last.focus(); }
    } else {
      if (document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }
});

document.addEventListener('keydown', e => {
  // Escape always closes modals/help
  if (e.key === 'Escape') {
    if (isModalOpen()) {
      e.preventDefault();
      // Close modals via their hide functions (to restore focus)
      if ($('modal-add-project').classList.contains('open')) { hideAddProject(); return; }
      if ($('modal-add').classList.contains('open')) { hideAddRoom(); return; }
      if ($('modal-tell').classList.contains('open')) { hideTell(); return; }
      if ($('modal-send').classList.contains('open')) { hideSend(); return; }
      closeAllModals();
      return;
    }
  }

  // Don't handle shortcuts when typing in inputs or modals are open
  if (isModalOpen() || isInputFocused()) return;

  // Terminal tab captures all keys for xterm
  if (currentTab === 'terminal') return;

  const key = e.key;

  // Global shortcuts
  if (key === '1') { e.preventDefault(); switchTab('overview'); return; }
  if (key === '2') { e.preventDefault(); switchTab('terminal'); return; }
  if (key === '?') { e.preventDefault(); toggleHelp(); return; }
  if (key === 'p') { e.preventDefault(); showAddProject(); return; }

  // Card navigation
  if (key === 'j' || key === 'ArrowDown') {
    e.preventDefault();
    focusCard(focusedCardIndex + 1);
    return;
  }
  if (key === 'k' || key === 'ArrowUp') {
    e.preventDefault();
    focusCard(focusedCardIndex - 1);
    return;
  }

  // Card-level shortcuts (when a card is focused)
  const card = document.activeElement;
  if (card && card.classList.contains('card')) {
    const roomName = card.dataset.room;
    if (!roomName) return;

    if (key === 'Enter' || key === 't') {
      e.preventDefault();
      openTerminal(roomName);
      return;
    }
    if (key === 'm') {
      e.preventDefault();
      showSend(roomName);
      return;
    }
  }
});

// Keep focusedCardIndex in sync when cards are clicked
document.addEventListener('focusin', e => {
  const card = e.target.closest('.card[tabindex]');
  if (card) {
    const cards = getCards();
    const idx = cards.indexOf(card);
    if (idx !== -1) focusedCardIndex = idx;
  }
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
init();
