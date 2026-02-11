import json
import os
import shutil
import subprocess
import sys

import click

from orc.room import Room
from orc.roles import default_role_content, ROLES_DIR
from orc.tmux import RoomSession, open_window, window_exists, attach_orc_session


def find_project_root(start=None):
    """Walk up from start (or cwd) to find the git repo root."""
    path = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


class OrcProject:
    def __init__(self, root):
        self.root = root
        self.orc_dir = os.path.join(root, ".orc")
        self.project_name = os.path.basename(root)

    def is_initialized(self):
        return os.path.isdir(self.orc_dir)

    def init(self, force=False):
        if self.is_initialized() and not force:
            click.echo("Error: .orc/ already exists. Use --force to reinitialize.", err=True)
            sys.exit(1)

        # Create .orc/ structure
        os.makedirs(self.orc_dir, exist_ok=True)

        # Create @main room
        main_room = Room(self.orc_dir, "@main")
        main_room.create(role="orchestrator")

        # Create default roles
        roles_dir = os.path.join(self.orc_dir, ROLES_DIR)
        os.makedirs(roles_dir, exist_ok=True)
        for role_name in ("orchestrator", "worker"):
            role_path = os.path.join(roles_dir, f"{role_name}.md")
            if not os.path.exists(role_path) or force:
                with open(role_path, "w") as f:
                    f.write(default_role_content(role_name))

        # Create .worktrees directory
        worktrees_dir = os.path.join(self.orc_dir, ".worktrees")
        os.makedirs(worktrees_dir, exist_ok=True)

        # Ensure .orc/.worktrees/ is in .gitignore
        self._ensure_gitignore()

    def _ensure_gitignore(self):
        gitignore_path = os.path.join(self.root, ".gitignore")
        entry = ".orc/.worktrees/"
        if os.path.exists(gitignore_path):
            with open(gitignore_path) as f:
                content = f.read()
            if entry in content:
                return
            with open(gitignore_path, "a") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(entry + "\n")
        else:
            with open(gitignore_path, "w") as f:
                f.write(entry + "\n")

    def add_room(self, room_name, role="worker"):
        """Create room files and worktree. Does not launch an agent."""
        # Validate name
        if room_name.startswith("@"):
            click.echo("Error: room names cannot start with '@' (reserved for @main)", err=True)
            sys.exit(1)
        if " " in room_name:
            click.echo("Error: room names cannot contain spaces", err=True)
            sys.exit(1)

        room = Room(self.orc_dir, room_name)
        if room.exists():
            click.echo(f"Error: room '{room_name}' already exists", err=True)
            sys.exit(1)

        # Create room files
        room.create(role=role, status="ready")

        # Create git worktree
        worktree_path = os.path.join(self.orc_dir, ".worktrees", room_name)
        try:
            subprocess.run(
                ["git", "worktree", "add", worktree_path, "-b", room_name, "HEAD"],
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            click.echo(f"Error creating worktree: {e.stderr.strip()}", err=True)
            room.delete()
            sys.exit(1)

        # Copy Claude Code permissions to worktree
        self._copy_claude_settings(worktree_path)

    def attach(self, room_name, role="worker", message=None):
        room = Room(self.orc_dir, room_name)

        # If room doesn't exist, create it first
        if not room.exists():
            click.echo(f"Room '{room_name}' not found, creating it...")
            self.add_room(room_name, role=role)

        tmux = RoomSession(self.project_name, room_name)
        if not tmux.is_alive():
            # Launch agent in tmux
            cwd = self._room_cwd(room_name)
            agent = room.read_agent()
            r = agent.get("role", "worker")
            role_path = os.path.join(self.orc_dir, ROLES_DIR, f"{r}.md")
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

        # Launch dashboard as a tmux window if not already running
        dash_name = ".orc-dash"
        if not window_exists(dash_name):
            open_window(dash_name, self.root, "orc _dash-server --port 7777")
            click.echo("orc dashboard -> http://localhost:7777")

        tmux.attach()
        attach_orc_session()

    def tell(self, room_name, message):
        """Send a message to a running agent's tmux session."""
        room = Room(self.orc_dir, room_name)
        if not room.exists():
            click.echo(f"Error: room '{room_name}' does not exist", err=True)
            sys.exit(1)
        tmux = RoomSession(self.project_name, room_name)
        if not tmux.is_alive():
            click.echo(f"Skipping '{room_name}' (not running)", err=True)
            return False
        tmux.send_keys(message)
        return True

    def tell_all(self, message):
        """Send a message to all running agents."""
        sent = []
        for entry in sorted(os.listdir(self.orc_dir)):
            if entry.startswith("."):
                continue
            room = Room(self.orc_dir, entry)
            if not room.exists():
                continue
            tmux = RoomSession(self.project_name, entry)
            if tmux.is_alive():
                tmux.send_keys(message)
                sent.append(entry)
        return sent

    def edit_room(self, room_name):
        room = Room(self.orc_dir, room_name)
        if not room.exists():
            click.echo(f"Error: room '{room_name}' does not exist", err=True)
            sys.exit(1)

        editor = os.environ.get("EDITOR", "vi")
        cwd = self._room_cwd(room_name)
        window_name = f"{self.project_name}-{room_name.lstrip('@')}-edit"
        open_window(window_name, cwd, f"{editor} .")
        attach_orc_session()

    def list_rooms(self):
        if not os.path.isdir(self.orc_dir):
            return

        rooms = []
        for entry in sorted(os.listdir(self.orc_dir)):
            if entry.startswith("."):
                continue
            room = Room(self.orc_dir, entry)
            if room.exists():
                status = room.read_status().get("status", "unknown")
                agent = room.read_agent()
                role = agent.get("role", "unknown")
                tmux = RoomSession(self.project_name, entry)
                alive = tmux.is_alive()
                rooms.append((entry, role, status, alive))

        if not rooms:
            click.echo("No rooms found.")
            return

        # Header
        click.echo(f"{'ROOM':<20} {'ROLE':<15} {'STATUS':<12} {'TMUX'}")
        click.echo("-" * 60)
        for name, role, status, alive in rooms:
            tmux_status = "alive" if alive else "dead"
            click.echo(f"{name:<20} {role:<15} {status:<12} {tmux_status}")

    def remove_room(self, room_name):
        if room_name == "@main":
            click.echo("Error: cannot remove @main", err=True)
            sys.exit(1)

        room = Room(self.orc_dir, room_name)
        if not room.exists():
            click.echo(f"Error: room '{room_name}' does not exist", err=True)
            sys.exit(1)

        # Kill tmux session
        tmux = RoomSession(self.project_name, room_name)
        tmux.kill()

        # Remove git worktree
        worktree_path = os.path.join(self.orc_dir, ".worktrees", room_name)
        if os.path.exists(worktree_path):
            subprocess.run(
                ["git", "worktree", "remove", worktree_path, "--force"],
                cwd=self.root,
                capture_output=True,
            )

        # Remove room files
        room.delete()

    def clean(self):
        """Remove read inbox messages and completed molecules from all rooms."""
        total_messages = 0
        total_molecules = 0

        for entry in sorted(os.listdir(self.orc_dir)):
            if entry.startswith("."):
                continue
            room = Room(self.orc_dir, entry)
            if not room.exists():
                continue

            # Clean read inbox messages
            inbox = room.read_inbox()
            if isinstance(inbox, list):
                unread = [m for m in inbox if not m.get("read")]
                removed = len(inbox) - len(unread)
                if removed > 0:
                    room._write_json("inbox.json", unread)
                    total_messages += removed

            # Clean completed molecules
            mol_dir = os.path.join(room.path, "molecules")
            if os.path.isdir(mol_dir):
                for fname in os.listdir(mol_dir):
                    if not fname.endswith(".json"):
                        continue
                    fpath = os.path.join(mol_dir, fname)
                    with open(fpath) as f:
                        mol = json.load(f)
                    atoms = mol.get("atoms", [])
                    if atoms and all(a.get("status") == "done" for a in atoms):
                        os.remove(fpath)
                        total_molecules += 1

        return total_messages, total_molecules

    def _copy_claude_settings(self, worktree_path):
        """Copy .claude/settings.local.json from project root to worktree."""
        src = os.path.join(self.root, ".claude", "settings.local.json")
        if not os.path.exists(src):
            return
        dst_dir = os.path.join(worktree_path, ".claude")
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src, os.path.join(dst_dir, "settings.local.json"))

    def _room_cwd(self, room_name):
        if room_name == "@main":
            return self.root
        return os.path.join(self.orc_dir, ".worktrees", room_name)
