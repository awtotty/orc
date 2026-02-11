"""orc web dashboard — stdlib HTTP server with inline single-page UI."""

import json
import mimetypes
import os
import re
import subprocess
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote

from orc.project import OrcProject
from orc.room import Room
from orc.roles import ROLES_DIR
from orc.tmux import RoomSession, session_exists, window_exists, open_window
from orc.universe import Universe

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _read_json(path):
    """Read a JSON file, returning {} on any error."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def discover_projects():
    """Return {name: abs_path} for every orc-initialised project."""
    uni = Universe()
    projects = uni.discover()

    # Also check cwd for a local project not in the universe
    from orc.project import find_project_root

    root = find_project_root()
    if root and os.path.isdir(os.path.join(root, ".orc")):
        projects.setdefault(os.path.basename(root), root)

    return projects


def _tmux_alive(project_name, room_name):
    """Check whether the tmux window for a room is alive."""
    window = f"{project_name}-{room_name.lstrip('@')}"
    try:
        r = subprocess.run(
            ["tmux", "list-windows", "-t", "orc", "-F", "#{window_name}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return r.returncode == 0 and window in r.stdout.strip().split("\n")
    except Exception:
        return False


def get_rooms(project_path):
    """List rooms with summary info."""
    orc_dir = os.path.join(project_path, ".orc")
    project_name = os.path.basename(project_path)
    rooms = []

    if not os.path.isdir(orc_dir):
        return rooms

    for entry in sorted(os.listdir(orc_dir)):
        if entry.startswith("."):
            continue
        room_dir = os.path.join(orc_dir, entry)
        if not os.path.isfile(os.path.join(room_dir, "agent.json")):
            continue

        agent = _read_json(os.path.join(room_dir, "agent.json"))
        status = _read_json(os.path.join(room_dir, "status.json"))
        inbox = _read_json(os.path.join(room_dir, "inbox.json"))
        inbox = inbox if isinstance(inbox, list) else []

        mol_dir = os.path.join(room_dir, "molecules")
        mol_count = 0
        if os.path.isdir(mol_dir):
            mol_count = len([f for f in os.listdir(mol_dir) if f.endswith(".json")])

        rooms.append(
            {
                "name": entry,
                "role": agent.get("role", "unknown"),
                "status": status.get("status", "unknown"),
                "tmux": _tmux_alive(project_name, entry),
                "inbox_count": len(inbox),
                "unread_count": sum(1 for m in inbox if not m.get("read")),
                "molecule_count": mol_count,
            }
        )

    return rooms


def get_inbox(project_path, room_name):
    """Return inbox messages for a room."""
    path = os.path.join(project_path, ".orc", room_name, "inbox.json")
    data = _read_json(path)
    return data if isinstance(data, list) else []


def get_molecules(project_path, room_name):
    """Return molecules for a room."""
    mol_dir = os.path.join(project_path, ".orc", room_name, "molecules")
    if not os.path.isdir(mol_dir):
        return []
    molecules = []
    for f in sorted(os.listdir(mol_dir)):
        if f.endswith(".json"):
            data = _read_json(os.path.join(mol_dir, f))
            if data:
                molecules.append(data)
    return molecules


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>orc</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0d1117;--surface:#161b22;--surface2:#1c2128;
  --border:#30363d;--text:#e6edf3;--muted:#8b949e;
  --accent:#58a6ff;--green:#3fb950;--yellow:#d29922;
  --red:#f85149;--purple:#bc8cff
}
body{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
  background:var(--bg);color:var(--text);display:flex;height:100vh;overflow:hidden
}
#sidebar{
  width:220px;min-width:220px;background:var(--surface);
  border-right:1px solid var(--border);display:flex;flex-direction:column
}
#sidebar h1{
  padding:20px;font-size:22px;font-weight:700;color:var(--accent);
  border-bottom:1px solid var(--border);letter-spacing:3px
}
.group-header{
  padding:12px 20px;font-size:12px;font-weight:600;color:var(--muted);
  text-transform:uppercase;letter-spacing:1px;cursor:pointer;
  display:flex;align-items:center;gap:6px;user-select:none
}
.group-header:hover{color:var(--text)}
.group-arrow{font-size:10px;transition:transform .15s}
.group-arrow.collapsed{transform:rotate(-90deg)}
.proj{
  padding:10px 20px 10px 34px;cursor:pointer;border-left:3px solid transparent;
  font-size:14px;transition:all .15s
}
.proj:hover{background:var(--surface2)}
.proj.active{background:var(--surface2);border-left-color:var(--accent);color:var(--accent)}
#project-list{overflow-y:auto;flex:1}
#content{flex:1;overflow-y:auto;padding:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:16px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden}
.card-head{padding:16px;display:flex;align-items:center;gap:10px}
.card-name{font-size:16px;font-weight:600;flex:1}
.badge{
  font-size:11px;padding:2px 8px;border-radius:10px;font-weight:500;
  text-transform:uppercase;letter-spacing:.5px
}
.s-active{background:rgba(63,185,80,.15);color:var(--green)}
.s-ready{background:rgba(210,153,34,.15);color:var(--yellow)}
.s-blocked{background:rgba(248,81,73,.15);color:var(--red)}
.s-done{background:rgba(63,185,80,.15);color:var(--green)}
.s-exited,.s-unknown{background:rgba(139,148,158,.15);color:var(--muted)}
.tmux{font-size:12px}
.tmux.alive{color:var(--green)}
.tmux.dead{color:var(--muted)}
.meta{padding:10px 16px;display:flex;gap:16px;font-size:13px;color:var(--muted)}
.toggle{
  display:block;width:100%;padding:10px 16px;background:0 0;border:0;
  border-top:1px solid var(--border);color:var(--muted);font-size:13px;
  cursor:pointer;text-align:left;font-family:inherit
}
.toggle:hover{color:var(--text)}
.panel{
  display:none;padding:12px 16px;border-top:1px solid var(--border);
  font-size:13px;max-height:300px;overflow-y:auto
}
.panel.open{display:block}
.msg{padding:8px 0;border-bottom:1px solid var(--border)}
.msg:last-child{border-bottom:0}
.msg-from{font-weight:600;color:var(--accent);font-size:12px}
.msg-ts{font-size:11px;color:var(--muted);margin-left:8px}
.msg-body{margin-top:4px;line-height:1.5;white-space:pre-wrap;word-break:break-word}
.msg.unread{border-left:2px solid var(--accent);padding-left:8px}
.mol-title{font-weight:600;margin-bottom:6px;color:var(--purple)}
.mol-sep{border-color:var(--border);margin:8px 0}
.atom{padding:6px 0;display:flex;align-items:center;gap:8px}
.atom-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.atom-dot.todo{background:var(--muted)}
.atom-dot.in_progress{background:var(--accent)}
.atom-dot.done{background:var(--green)}
.empty{color:var(--muted);font-style:italic;padding:8px 0}
#placeholder{
  display:flex;align-items:center;justify-content:center;
  height:100%;color:var(--muted);font-size:18px
}
.dot{
  position:fixed;top:12px;right:12px;width:8px;height:8px;border-radius:50%;
  background:var(--green);opacity:.5;transition:opacity .3s
}
.dot.loading{opacity:1;background:var(--accent)}
.content-header{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.content-header h2{font-size:20px;font-weight:600;flex:1}
.btn{
  padding:6px 14px;border-radius:6px;border:1px solid var(--border);
  background:var(--surface2);color:var(--text);font-size:13px;cursor:pointer;
  font-family:inherit;transition:all .15s
}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-danger{color:var(--red);font-size:12px;padding:4px 10px}
.btn-danger:hover{border-color:var(--red);background:rgba(248,81,73,.1)}
.modal-bg{
  position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;
  align-items:center;justify-content:center;z-index:100;display:none
}
.modal-bg.open{display:flex}
.modal{
  background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:24px;width:360px;max-width:90vw
}
.modal h3{margin-bottom:16px;font-size:16px}
.modal label{display:block;font-size:13px;color:var(--muted);margin-bottom:4px}
.modal input,.modal select{
  width:100%;padding:8px 10px;border-radius:6px;border:1px solid var(--border);
  background:var(--surface2);color:var(--text);font-size:14px;font-family:inherit;
  margin-bottom:12px
}
.modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:8px}
</style>
</head>
<body>
<nav id="sidebar">
  <h1>orc</h1>
  <div id="project-list"></div>
</nav>
<div id="content">
  <div id="placeholder">Select a project</div>
  <div id="content-header" class="content-header" style="display:none">
    <h2 id="project-title"></h2>
    <button class="btn" onclick="showAddRoom()">+ Add Room</button>
  </div>
  <div id="rooms" class="grid" style="display:none"></div>
</div>
<div class="modal-bg" id="modal-add">
  <div class="modal">
    <h3>Add Room</h3>
    <label>Room name</label>
    <input id="add-room-name" placeholder="e.g. worker-2">
    <label>Role</label>
    <select id="add-room-role"><option value="worker">worker</option><option value="orchestrator">orchestrator</option></select>
    <div class="modal-actions">
      <button class="btn" onclick="hideAddRoom()">Cancel</button>
      <button class="btn" onclick="doAddRoom()">Add</button>
    </div>
  </div>
</div>
<div class="dot" id="dot"></div>
<script>
const $=id=>document.getElementById(id);
const api=p=>fetch(p).then(r=>r.json());
const enc=encodeURIComponent;
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}

let projects={};
let selected=null;
let expanded={};
let selVersion=0;

async function init(){
  projects=await api('/api/projects');
  renderSidebar();
  const names=Object.keys(projects);
  const saved=localStorage.getItem('orc-selected-project');
  if(names.length&&!selected) await sel(saved&&names.includes(saved)?saved:names[0]);
  setInterval(refresh,5000);
}

let sidebarCollapsed=false;
function renderSidebar(){
  const el=$('project-list');
  el.innerHTML='';
  const names=Object.keys(projects);
  if(!names.length) return;
  const hdr=document.createElement('div');
  hdr.className='group-header';
  hdr.innerHTML='<span class="group-arrow'+(sidebarCollapsed?' collapsed':'')+'">&#9660;</span> orc';
  hdr.onclick=()=>{sidebarCollapsed=!sidebarCollapsed;renderSidebar()};
  el.appendChild(hdr);
  if(sidebarCollapsed) return;
  for(const name of names){
    const d=document.createElement('div');
    d.className='proj'+(selected===name?' active':'');
    d.textContent=name;
    d.onclick=()=>sel(name);
    el.appendChild(d);
  }
}

async function sel(name){
  selected=name;expanded={};
  selVersion++;
  localStorage.setItem('orc-selected-project',name);
  renderSidebar();
  await loadRooms();
}

async function loadRooms(){
  if(!selected) return;
  const v=selVersion;
  const rooms=await api('/api/projects/'+enc(selected)+'/rooms');
  if(v!==selVersion) return;
  renderRooms(rooms);
}

function renderRooms(rooms){
  $('placeholder').style.display='none';
  $('content-header').style.display='flex';
  $('project-title').textContent=selected;
  const el=$('rooms');
  el.style.display='grid';
  el.innerHTML=rooms.map(r=>{
    const sc='s-'+r.status;
    const tc=r.tmux?'alive':'dead';
    const tl=r.tmux?'\u25cf live':'\u25cb dead';
    const n=esc(r.name);
    const isMain=r.name==='@main';
    return '<div class="card">'+
      '<div class="card-head">'+
        '<span class="card-name">'+n+'</span>'+
        '<span class="badge '+sc+'">'+r.status+'</span>'+
        '<span class="tmux '+tc+'">'+tl+'</span>'+
        (isMain?'':'<button class="btn btn-danger" onclick="doRmRoom(\''+n.replace(/'/g,"\\'")+'\')">Remove</button>')+
      '</div>'+
      '<div class="meta">'+
        '<span>'+esc(r.role)+'</span>'+
        '<span>\u2709 '+r.inbox_count+(r.unread_count?' ('+r.unread_count+' new)':'')+'</span>'+
        '<span>\u25c6 '+r.molecule_count+'</span>'+
      '</div>'+
      '<button class="toggle" data-room="'+n+'" data-section="inbox">\u25bc Inbox</button>'+
      '<div class="panel open" data-panel="'+n+'|inbox"></div>'+
      '<button class="toggle" data-room="'+n+'" data-section="molecules">\u25bc Molecules</button>'+
      '<div class="panel open" data-panel="'+n+'|molecules"></div>'+
    '</div>';
  }).join('');
  // Load panel contents for all rooms
  for(const r of rooms){
    const n=esc(r.name);
    expanded[n+'|inbox']=true;
    expanded[n+'|molecules']=true;
    const ip=document.querySelector('[data-panel="'+CSS.escape(n+'|inbox')+'"]');
    const mp=document.querySelector('[data-panel="'+CSS.escape(n+'|molecules')+'"]');
    if(ip) loadPanel(n,'inbox',ip);
    if(mp) loadPanel(n,'molecules',mp);
  }
}

document.addEventListener('click',async e=>{
  const btn=e.target.closest('.toggle');
  if(!btn) return;
  const room=btn.dataset.room;
  const section=btn.dataset.section;
  const key=room+'|'+section;
  const panel=document.querySelector('[data-panel="'+CSS.escape(key)+'"]');
  if(!panel) return;

  if(panel.classList.contains('open')){
    panel.classList.remove('open');
    btn.textContent='\u25b6 '+cap(section);
    delete expanded[key];
    return;
  }

  await loadPanel(room,section,panel);
  panel.classList.add('open');
  btn.textContent='\u25bc '+cap(section);
  expanded[key]=true;
});

async function loadPanel(room,section,panel){
  if(section==='inbox'){
    const msgs=await api('/api/projects/'+enc(selected)+'/rooms/'+enc(room)+'/inbox');
    if(!msgs.length){
      panel.innerHTML='<div class="empty">No messages</div>';
    }else{
      panel.innerHTML=msgs.map(m=>
        '<div class="msg'+(m.read?'':' unread')+'">'+
          '<span class="msg-from">'+esc(m.from||'?')+'</span>'+
          '<span class="msg-ts">'+esc(m.ts||'')+'</span>'+
          '<div class="msg-body">'+esc(m.message||'')+'</div>'+
        '</div>'
      ).join('');
    }
  }else{
    const mols=await api('/api/projects/'+enc(selected)+'/rooms/'+enc(room)+'/molecules');
    if(!mols.length){
      panel.innerHTML='<div class="empty">No molecules</div>';
    }else{
      panel.innerHTML=mols.map(mol=>{
        let h='<div class="mol-title">'+esc(mol.title||mol.id||'Untitled');
        if(mol.status) h+=' <span class="badge s-'+(mol.status==='in_progress'?'active':mol.status)+'">'+mol.status+'</span>';
        h+='</div>';
        if(mol.atoms){
          h+=mol.atoms.map(a=>
            '<div class="atom">'+
              '<span class="atom-dot '+(a.status||'todo')+'"></span>'+
              '<span>'+esc(a.title||a.id)+'</span>'+
              '<span class="badge s-'+(a.status==='in_progress'?'active':(a.status||'unknown'))+'">'+
                (a.status||'?')+
              '</span>'+
            '</div>'
          ).join('');
        }
        return h;
      }).join('<hr class="mol-sep">');
    }
  }
}

function cap(s){return s[0].toUpperCase()+s.slice(1)}

async function refresh(){
  const dot=$('dot');
  dot.classList.add('loading');
  try{
    projects=await api('/api/projects');
    renderSidebar();
    if(selected&&projects[selected]){
      await loadRooms();
      for(const key of Object.keys(expanded)){
        const i=key.lastIndexOf('|');
        const room=key.slice(0,i);
        const section=key.slice(i+1);
        const panel=document.querySelector('[data-panel="'+CSS.escape(key)+'"]');
        const btn=document.querySelector('.toggle[data-room="'+CSS.escape(room)+'"][data-section="'+CSS.escape(section)+'"]');
        if(panel&&btn){
          await loadPanel(room,section,panel);
          panel.classList.add('open');
          btn.textContent='\u25bc '+cap(section);
        }
      }
    }
  }catch(e){}
  dot.classList.remove('loading');
}

function showAddRoom(){$('modal-add').classList.add('open');$('add-room-name').value='';$('add-room-name').focus()}
function hideAddRoom(){$('modal-add').classList.remove('open')}
async function doAddRoom(){
  const name=$('add-room-name').value.trim();
  const role=$('add-room-role').value;
  if(!name) return;
  hideAddRoom();
  await fetch('/api/projects/'+enc(selected)+'/rooms/add',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({room_name:name,role:role})
  });
  await loadRooms();
}
async function doRmRoom(name){
  if(!confirm('Remove room "'+name+'"? This will delete the worktree and kill its tmux session.')) return;
  await fetch('/api/projects/'+enc(selected)+'/rooms/'+enc(name)+'/rm',{method:'POST'});
  await loadRooms();
}

init();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

ROUTES = [
    (re.compile(r"^/$"), "dashboard"),
    (re.compile(r"^/favicon\.ico$"), "favicon"),
    (re.compile(r"^/static/(.+)$"), "static_file"),
    (re.compile(r"^/api/projects$"), "projects"),
    (re.compile(r"^/api/projects/([^/]+)/rooms$"), "rooms"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/inbox$"), "inbox"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/molecules$"), "molecules"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/terminal$"), "terminal"),
]

POST_ROUTES = [
    (re.compile(r"^/api/projects/([^/]+)/rooms/add$"), "add_room"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/rm$"), "rm_room"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/attach$"), "attach"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/tell$"), "tell"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/send$"), "send_msg"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/status$"), "set_status"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/kill$"), "kill"),
    (re.compile(r"^/api/projects/([^/]+)/clean$"), "clean"),
]


class OrcHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = unquote(self.path.split("?")[0])
        for pattern, name in ROUTES:
            m = pattern.match(path)
            if m:
                getattr(self, "_handle_" + name)(*m.groups())
                return
        self._respond(404, "text/plain", "Not found")

    def do_POST(self):
        path = unquote(self.path.split("?")[0])
        for pattern, name in POST_ROUTES:
            m = pattern.match(path)
            if m:
                getattr(self, "_post_" + name)(*m.groups())
                return
        self._respond(404, "text/plain", "Not found")

    def do_OPTIONS(self):
        try:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
        except BrokenPipeError:
            pass

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw)

    def _post_add_room(self, project_name):
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        body = self._read_body()
        room_name = body.get("room_name", "").strip()
        role = body.get("role", "worker").strip() or "worker"
        if not room_name:
            self._json({"error": "room_name is required"}, 400)
            return
        proj = OrcProject(projects[project_name])
        try:
            proj.add_room(room_name, role=role)
        except SystemExit:
            self._json({"error": f"failed to add room '{room_name}'"}, 400)
            return
        self._json({"ok": True, "room": room_name})

    def _post_rm_room(self, project_name, room_name):
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        proj = OrcProject(projects[project_name])
        try:
            proj.remove_room(room_name)
        except SystemExit:
            self._json({"error": f"failed to remove room '{room_name}'"}, 400)
            return
        self._json({"ok": True})

    def _post_attach(self, project_name, room_name):
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        body = self._read_body()
        role = body.get("role", "worker")
        message = body.get("message")
        proj = OrcProject(projects[project_name])
        room = Room(proj.orc_dir, room_name)

        # Create room if needed
        if not room.exists():
            try:
                proj.add_room(room_name, role=role)
            except SystemExit:
                self._json({"error": f"failed to create room '{room_name}'"}, 400)
                return

        # Ensure tmux session exists
        if not session_exists():
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", "orc", "-c", proj.root],
                check=True, capture_output=True,
            )

        tmux = RoomSession(proj.project_name, room_name)
        if not tmux.is_alive():
            cwd = proj._room_cwd(room_name)
            agent = room.read_agent()
            r = agent.get("role", "worker")
            role_path = os.path.join(proj.orc_dir, ROLES_DIR, f"{r}.md")
            role_prompt = ""
            if os.path.exists(role_path):
                with open(role_path) as f:
                    role_prompt = f.read()
            tmux.create(cwd=cwd)
            tmux.start_claude(role_prompt)
            room.set_status("active")

            if message:
                import time
                time.sleep(3)
                tmux.send_keys(message)

        self._json({"ok": True})

    def _post_tell(self, project_name, room_name):
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        body = self._read_body()
        message = body.get("message", "")
        if not message:
            self._json({"error": "message is required"}, 400)
            return
        proj = OrcProject(projects[project_name])
        ok = proj.tell(room_name, message)
        if not ok:
            self._json({"error": f"room '{room_name}' is not running"}, 400)
            return
        self._json({"ok": True})

    def _post_send_msg(self, project_name, room_name):
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        body = self._read_body()
        message = body.get("message", "")
        from_addr = body.get("from", "web-ui")
        if not message:
            self._json({"error": "message is required"}, 400)
            return
        proj = OrcProject(projects[project_name])
        inbox_path = os.path.join(proj.orc_dir, room_name, "inbox.json")
        if not os.path.isfile(inbox_path):
            self._json({"error": f"room '{room_name}' not found"}, 404)
            return
        with open(inbox_path) as f:
            inbox = json.load(f)
        if not isinstance(inbox, list):
            inbox = []
        inbox.append({
            "from": from_addr,
            "message": message,
            "read": False,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        with open(inbox_path, "w") as f:
            json.dump(inbox, f, indent=2)
            f.write("\n")
        self._json({"ok": True})

    def _post_set_status(self, project_name, room_name):
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        body = self._read_body()
        status = body.get("status", "")
        valid = {"active", "ready", "blocked", "done", "exited"}
        if status not in valid:
            self._json({"error": f"status must be one of: {', '.join(sorted(valid))}"}, 400)
            return
        proj = OrcProject(projects[project_name])
        room = Room(proj.orc_dir, room_name)
        if not room.exists():
            self._json({"error": f"room '{room_name}' not found"}, 404)
            return
        room.set_status(status)
        self._json({"ok": True})

    def _post_clean(self, project_name):
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        proj = OrcProject(projects[project_name])
        messages, molecules = proj.clean()
        self._json({"messages": messages, "molecules": molecules})

    def _post_kill(self, project_name, room_name):
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        proj = OrcProject(projects[project_name])
        tmux = RoomSession(proj.project_name, room_name)
        tmux.kill()
        self._json({"ok": True})

    def _handle_dashboard(self):
        index_path = os.path.join(STATIC_DIR, "index.html")
        if os.path.isfile(index_path):
            with open(index_path) as f:
                self._respond(200, "text/html", f.read())
        else:
            self._respond(200, "text/html", DASHBOARD_HTML)

    def _handle_favicon(self):
        self._respond(204, "text/plain", "")

    def _handle_static_file(self, filepath):
        # Prevent directory traversal
        safe = os.path.normpath(filepath)
        if safe.startswith("..") or os.path.isabs(safe):
            self._respond(403, "text/plain", "Forbidden")
            return
        full_path = os.path.join(STATIC_DIR, safe)
        if not os.path.isfile(full_path):
            self._respond(404, "text/plain", "Not found")
            return
        content_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
        with open(full_path, "rb") as f:
            data = f.read()
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def _handle_terminal(self, project_name, room_name):
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        target = f"orc:{project_name}-{room_name.lstrip('@')}"
        alive = _tmux_alive(project_name, room_name)
        content = ""
        if alive:
            try:
                r = subprocess.run(
                    ["tmux", "capture-pane", "-t", target, "-p", "-S", "-500"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    content = r.stdout
            except Exception:
                pass
        self._json({"content": content, "alive": alive})

    def _handle_projects(self):
        self._json(discover_projects())

    def _handle_rooms(self, project_name):
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        self._json(get_rooms(projects[project_name]))

    def _handle_inbox(self, project_name, room_name):
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        self._json(get_inbox(projects[project_name], room_name))

    def _handle_molecules(self, project_name, room_name):
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        self._json(get_molecules(projects[project_name], room_name))

    def _json(self, data, status=200):
        body = json.dumps(data)
        self._respond(status, "application/json", body)

    def _respond(self, status, content_type, body):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body.encode())
        except BrokenPipeError:
            pass

    def log_message(self, fmt, *args):
        pass  # suppress default request logging


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def run_server(port=7777):
    host = "0.0.0.0" if os.environ.get("ORC_SANDBOX") else "127.0.0.1"

    # Start WebSocket terminal server in background thread
    ws_port = port + 1
    try:
        import asyncio
        import threading
        from orc.terminal import run_terminal_server

        def _run_ws():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(run_terminal_server(host=host, port=ws_port))

        threading.Thread(target=_run_ws, daemon=True).start()
        print(f"orc terminal ws \u2192 ws://localhost:{ws_port}")
    except ImportError:
        pass  # websockets not installed, skip terminal server

    server = HTTPServer((host, port), OrcHandler)
    print(f"orc dashboard \u2192 http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        server.shutdown()


if __name__ == "__main__":
    import sys
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 7777
    run_server(p)
