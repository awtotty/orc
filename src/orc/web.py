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
    (re.compile(r"^/api/projects/add$"), "add_project"),
    (re.compile(r"^/api/projects/([^/]+)/rm$"), "rm_project"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/add$"), "add_room"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/rm$"), "rm_room"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/attach$"), "attach"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/tell$"), "tell"),
    (re.compile(r"^/api/projects/([^/]+)/rooms/([^/]+)/terminal/input$"), "terminal_input"),
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

    def _post_add_project(self):
        body = self._read_body()
        path = body.get("path", "").strip()
        name = body.get("name", "").strip() or None
        if not path:
            self._json({"error": "path is required"}, 400)
            return
        try:
            registered = Universe().add_project(path, name=name)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
            return
        # Auto-initialize if not yet an orc project
        real = os.path.realpath(path)
        if not os.path.isdir(os.path.join(real, ".orc")):
            OrcProject(real).init()
        self._json({"ok": True, "name": registered})

    def _post_rm_project(self, project_name):
        try:
            Universe().remove_project(project_name)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
            return
        self._json({"ok": True})

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
            room.set_status("working")

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
        try:
            ok = proj.tell(room_name, message)
        except SystemExit:
            self._json({"error": f"room '{room_name}' not found"}, 404)
            return
        if not ok:
            self._json({"error": f"room '{room_name}' is not running"}, 400)
            return
        self._json({"ok": True})

    def _post_terminal_input(self, project_name, room_name):
        """Send raw terminal input to a room's tmux pane (no Enter appended)."""
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        body = self._read_body()
        data = body.get("data", "")
        if not data:
            self._json({"error": "data is required"}, 400)
            return
        target = f"orc:{project_name}-{room_name.lstrip('@')}"
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "-l", data],
                check=True, capture_output=True, timeout=5,
            )
            self._json({"ok": True})
        except Exception:
            self._json({"error": "failed to send input"}, 500)

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
        valid = {"idle", "working", "blocked", "done", "exited"}
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
            self._respond(500, "text/plain",
                          "Dashboard not found. Expected: " + index_path)

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
