"""orc web dashboard — stdlib HTTP server with inline single-page UI."""

import json
import mimetypes
import os
import re
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote

from orc.project import OrcProject
from orc.room import Room
from orc.service import (
    attach_room,
    capture_terminal,
    discover_projects,
    get_inbox,
    get_molecules,
    get_rooms,
    send_inbox_message,
)
from orc.tmux import RoomSession
from orc.universe import Universe

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


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
    (re.compile(r"^/api/shutdown$"), "shutdown"),
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
        url = body.get("url", "").strip()
        name = body.get("name", "").strip() or None
        if not url:
            self._json({"error": "url is required"}, 400)
            return
        # Derive name from URL if not provided
        if name is None:
            name = url.rstrip("/").rsplit("/", 1)[-1]
            if name.endswith(".git"):
                name = name[:-4]
        if not name:
            self._json({"error": "could not derive project name from URL"}, 400)
            return
        uni = Universe()
        uni.ensure_dir()
        dest = os.path.join(uni.projects_dir, name)
        if os.path.exists(dest):
            self._json({"error": f"project '{name}' already exists"}, 400)
            return
        try:
            r = subprocess.run(
                ["git", "clone", url, dest],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode != 0:
                self._json({"error": r.stderr.strip() or "git clone failed"}, 400)
                return
        except subprocess.TimeoutExpired:
            self._json({"error": "git clone timed out"}, 400)
            return
        OrcProject(dest).init()
        self._json({"ok": True, "name": name})

    def _post_rm_project(self, project_name):
        if project_name == "orc":
            self._json({"error": "cannot remove the orc project"}, 400)
            return
        import shutil
        uni = Universe()
        entry = os.path.join(uni.projects_dir, project_name)
        if not os.path.exists(entry) and not os.path.islink(entry):
            self._json({"error": f"project '{project_name}' not found"}, 400)
            return
        if os.path.islink(entry):
            os.unlink(entry)
        else:
            shutil.rmtree(entry)
        self._json({"ok": True})

    def _post_add_room(self, project_name):
        projects = discover_projects()
        if project_name not in projects:
            self._json({"error": "project not found"}, 404)
            return
        body = self._read_body()
        room_name = body.get("room_name", "").strip()
        role = body.get("role", "worker").strip() or "worker"
        model = body.get("model", "").strip() or None
        if not room_name:
            self._json({"error": "room_name is required"}, 400)
            return
        proj = OrcProject(projects[project_name])
        try:
            proj.add_room(room_name, role=role, model=model)
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
        model = body.get("model") or None
        message = body.get("message")
        try:
            attach_room(projects[project_name], room_name, role=role, model=model, message=message)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
            return
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
        try:
            send_inbox_message(projects[project_name], room_name, message, from_addr)
        except ValueError as e:
            self._json({"error": str(e)}, 404)
            return
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

    def _post_shutdown(self):
        import signal, threading
        self._json({"ok": True})
        # Delay slightly so the HTTP response flushes before the container dies
        threading.Timer(0.5, lambda: os.kill(1, signal.SIGTERM)).start()

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
        content, alive = capture_terminal(project_name, room_name)
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
        from orc.web.terminal import run_terminal_server

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
