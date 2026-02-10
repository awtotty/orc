"""orc web dashboard — stdlib HTTP server with inline single-page UI."""

import json
import os
import re
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote

from orc.roles import _ORC_ROOT

PROJECTS_DIR = os.path.join(_ORC_ROOT, "projects")


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
    projects = {}

    # Scan PROJECTS_DIR
    if os.path.isdir(PROJECTS_DIR):
        for entry in sorted(os.listdir(PROJECTS_DIR)):
            p = os.path.join(PROJECTS_DIR, entry)
            if os.path.isdir(os.path.join(p, ".orc")):
                projects[entry] = p

    # Walk up from cwd to find git root
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
.proj{
  padding:12px 20px;cursor:pointer;border-left:3px solid transparent;
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
</style>
</head>
<body>
<nav id="sidebar">
  <h1>orc</h1>
  <div id="project-list"></div>
</nav>
<div id="content">
  <div id="placeholder">Select a project</div>
  <div id="rooms" class="grid" style="display:none"></div>
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

async function init(){
  projects=await api('/api/projects');
  renderSidebar();
  const names=Object.keys(projects);
  if(names.length&&!selected) await sel(names[0]);
  setInterval(refresh,5000);
}

function renderSidebar(){
  const el=$('project-list');
  el.innerHTML='';
  for(const name of Object.keys(projects)){
    const d=document.createElement('div');
    d.className='proj'+(selected===name?' active':'');
    d.textContent=name;
    d.onclick=()=>sel(name);
    el.appendChild(d);
  }
}

async function sel(name){
  selected=name;expanded={};
  renderSidebar();
  await loadRooms();
}

async function loadRooms(){
  if(!selected) return;
  const rooms=await api('/api/projects/'+enc(selected)+'/rooms');
  renderRooms(rooms);
}

function renderRooms(rooms){
  $('placeholder').style.display='none';
  const el=$('rooms');
  el.style.display='grid';
  el.innerHTML=rooms.map(r=>{
    const sc='s-'+r.status;
    const tc=r.tmux?'alive':'dead';
    const tl=r.tmux?'\u25cf live':'\u25cb dead';
    const n=esc(r.name);
    return '<div class="card">'+
      '<div class="card-head">'+
        '<span class="card-name">'+n+'</span>'+
        '<span class="badge '+sc+'">'+r.status+'</span>'+
        '<span class="tmux '+tc+'">'+tl+'</span>'+
      '</div>'+
      '<div class="meta">'+
        '<span>'+esc(r.role)+'</span>'+
        '<span>\u2709 '+r.inbox_count+(r.unread_count?' ('+r.unread_count+' new)':'')+'</span>'+
        '<span>\u25c6 '+r.molecule_count+'</span>'+
      '</div>'+
      '<button class="toggle" data-room="'+n+'" data-section="inbox">\u25b6 Inbox</button>'+
      '<div class="panel" data-panel="'+n+'|inbox"></div>'+
      '<button class="toggle" data-room="'+n+'" data-section="molecules">\u25b6 Molecules</button>'+
      '<div class="panel" data-panel="'+n+'|molecules"></div>'+
    '</div>';
  }).join('');
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

init();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

ROUTES = [
    (re.compile(r"^/$"), "dashboard"),
    (re.compile(r"^/api/projects$"), "projects"),
    (re.compile(r"^/api/projects/([^/]+)/rooms$"), "rooms"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/inbox$"), "inbox"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/molecules$"), "molecules"),
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

    def _handle_dashboard(self):
        self._respond(200, "text/html", DASHBOARD_HTML)

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
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, fmt, *args):
        pass  # suppress default request logging


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def run_server(port=7777):
    server = HTTPServer(("127.0.0.1", port), OrcHandler)
    print(f"orc dashboard \u2192 http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        server.shutdown()
