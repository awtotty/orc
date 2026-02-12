"""orc service layer — shared business logic extracted from web/cli/dashboard."""

import json
import os
import subprocess
from datetime import datetime, timezone

from orc.backend import resolve_backend
from orc.config import load as load_config
from orc.project import OrcProject, find_project_root
from orc.room import Room
from orc.roles import ROLES_DIR
from orc.tmux import RoomSession, session_exists
from orc.universe import Universe


# ---------------------------------------------------------------------------
# Project discovery
# ---------------------------------------------------------------------------


def discover_projects():
    """Return {name: abs_path} for every orc-initialised project.

    Includes projects from the universe and the local project (if any).
    """
    uni = Universe()
    projects = uni.discover()

    root = find_project_root()
    if root and os.path.isdir(os.path.join(root, ".orc")):
        projects.setdefault(os.path.basename(root), root)

    return projects


# ---------------------------------------------------------------------------
# Room queries
# ---------------------------------------------------------------------------


def get_rooms(project_path):
    """List rooms with summary info for a project.

    Returns a list of dicts with keys:
        name, role, status, tmux, inbox_count, unread_count, molecule_count
    """
    orc_dir = os.path.join(project_path, ".orc")
    project_name = os.path.basename(project_path)
    rooms = []

    if not os.path.isdir(orc_dir):
        return rooms

    for entry in sorted(os.listdir(orc_dir)):
        if entry.startswith("."):
            continue
        room = Room(orc_dir, entry)
        if not room.exists():
            continue

        agent = room.read_agent()
        status_data = room.read_status()
        inbox = room.read_inbox()
        inbox = inbox if isinstance(inbox, list) else []

        mol_dir = os.path.join(room.path, "molecules")
        mol_count = 0
        if os.path.isdir(mol_dir):
            mol_count = len([f for f in os.listdir(mol_dir) if f.endswith(".json")])

        rooms.append(
            {
                "name": entry,
                "role": agent.get("role", "unknown"),
                "model": agent.get("model"),
                "backend": agent.get("backend"),
                "status": status_data.get("status", "unknown"),
                "tmux": tmux_alive(project_name, entry),
                "inbox_count": len(inbox),
                "unread_count": sum(1 for m in inbox if not m.get("read")),
                "molecule_count": mol_count,
            }
        )

    return rooms


def get_inbox(project_path, room_name):
    """Return inbox messages for a room."""
    room = Room(os.path.join(project_path, ".orc"), room_name)
    data = room.read_inbox()
    return data if isinstance(data, list) else []


def get_molecules(project_path, room_name):
    """Return molecules for a room."""
    mol_dir = os.path.join(project_path, ".orc", room_name, "molecules")
    if not os.path.isdir(mol_dir):
        return []
    molecules = []
    for f in sorted(os.listdir(mol_dir)):
        if f.endswith(".json"):
            path = os.path.join(mol_dir, f)
            try:
                with open(path) as fh:
                    data = json.load(fh)
                if data:
                    molecules.append(data)
            except (json.JSONDecodeError, OSError):
                pass
    return molecules


# ---------------------------------------------------------------------------
# Inbox messaging
# ---------------------------------------------------------------------------


def send_inbox_message(project_path, room_name, message, from_addr="cli"):
    """Append a message to a room's inbox.

    Raises ValueError if the room does not exist.
    """
    inbox_path = os.path.join(project_path, ".orc", room_name, "inbox.json")

    if not os.path.isfile(inbox_path):
        raise ValueError(f"Room '{room_name}' not found")

    with open(inbox_path) as f:
        inbox = json.load(f)

    if not isinstance(inbox, list):
        inbox = []

    inbox.append(
        {
            "from": from_addr,
            "message": message,
            "read": False,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )

    with open(inbox_path, "w") as f:
        json.dump(inbox, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# Tmux helpers
# ---------------------------------------------------------------------------


def tmux_alive(project_name, room_name):
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


# ---------------------------------------------------------------------------
# Room attach
# ---------------------------------------------------------------------------


def attach_room(project_path, room_name, role="worker", model=None, message=None):
    """Ensure a room exists, has a tmux window, and is running an agent.

    This is the headless/API version — it does NOT attach the terminal
    to the tmux session (callers like the CLI can do that separately).

    Returns True on success.
    Raises ValueError on failure.
    """
    proj = OrcProject(project_path)
    room = Room(proj.orc_dir, room_name)

    if not room.exists():
        try:
            proj.add_room(room_name, role=role, model=model)
        except SystemExit:
            raise ValueError(f"Failed to create room '{room_name}'")

    if not session_exists():
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", "orc", "-c", proj.root],
            check=True,
            capture_output=True,
        )

    tmux = RoomSession(proj.project_name, room_name)
    if not tmux.is_alive():
        cwd = proj._room_cwd(room_name)
        agent = room.read_agent()
        r = agent.get("role", "worker")
        # Model resolution: explicit param > agent.json > default
        effective_model = model or agent.get("model")
        backend = resolve_backend(agent, load_config())
        role_path = os.path.join(proj.orc_dir, ROLES_DIR, f"{r}.md")
        role_prompt = ""
        if os.path.exists(role_path):
            with open(role_path) as f:
                role_prompt = f.read()
        tmux.create(cwd=cwd)
        tmux.start_agent(backend, role_prompt, model=effective_model, cwd=cwd)
        room.set_status("working")

        if message:
            import time

            time.sleep(3)
            tmux.send_keys(message)

    return True


# ---------------------------------------------------------------------------
# Terminal capture
# ---------------------------------------------------------------------------


def capture_terminal(project_name, room_name):
    """Capture the tmux pane content for a room.

    Returns (content, alive) tuple.
    """
    target = f"orc:{project_name}-{room_name.lstrip('@')}"
    alive = tmux_alive(project_name, room_name)
    content = ""
    if alive:
        try:
            r = subprocess.run(
                ["tmux", "capture-pane", "-t", target, "-p", "-S", "-500"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                content = r.stdout
        except Exception:
            pass
    return content, alive
