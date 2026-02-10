"""Universe — the orc/projects/ directory holding all orc projects."""

import json
import os
from datetime import datetime, timezone

from orc.roles import _ORC_ROOT

PROJECTS_DIR = os.path.join(_ORC_ROOT, "projects")


class Universe:
    def __init__(self):
        self.projects_dir = PROJECTS_DIR

    def ensure_dir(self):
        os.makedirs(self.projects_dir, exist_ok=True)

    def discover(self):
        """Return {name: abs_path} for all initialized projects."""
        projects = {}
        if not os.path.isdir(self.projects_dir):
            return projects
        for entry in sorted(os.listdir(self.projects_dir)):
            p = os.path.join(self.projects_dir, entry)
            real = os.path.realpath(p)
            if os.path.isdir(real) and os.path.isdir(os.path.join(real, ".orc")):
                projects[entry] = real
        return projects

    def all_projects(self):
        """Return {name: abs_path} including uninitialized projects."""
        projects = {}
        if not os.path.isdir(self.projects_dir):
            return projects
        for entry in sorted(os.listdir(self.projects_dir)):
            p = os.path.join(self.projects_dir, entry)
            real = os.path.realpath(p)
            if os.path.isdir(real):
                projects[entry] = real
        return projects

    def add_project(self, path, name=None):
        """Register a project in the universe via symlink."""
        real = os.path.realpath(path)
        if not os.path.isdir(real):
            raise ValueError(f"Path does not exist: {path}")
        if not os.path.isdir(os.path.join(real, ".git")):
            raise ValueError(f"Not a git repository: {path}")

        if name is None:
            name = os.path.basename(real)

        self.ensure_dir()
        link = os.path.join(self.projects_dir, name)
        if os.path.exists(link):
            raise ValueError(f"Project '{name}' already exists in the universe")

        os.symlink(real, link)
        return name

    def remove_project(self, name):
        """Remove a project from the universe (symlinks only)."""
        link = os.path.join(self.projects_dir, name)
        if not os.path.exists(link) and not os.path.islink(link):
            raise ValueError(f"Project '{name}' not in the universe")
        if os.path.islink(link):
            os.unlink(link)
        else:
            raise ValueError(
                f"'{name}' is not a symlink — remove manually to avoid data loss"
            )

    def resolve_project(self, name):
        """Get absolute path for a project by name."""
        p = os.path.join(self.projects_dir, name)
        real = os.path.realpath(p)
        if os.path.isdir(real) and os.path.isdir(os.path.join(real, ".orc")):
            return real
        raise ValueError(f"Project '{name}' not found or not initialized")

    def send_message(self, from_addr, to_project, to_room, message):
        """Send a message to a room in a project.

        from_addr: sender identifier (e.g. "project/room" or "cli")
        to_project: target project name
        to_room: target room name
        message: message text
        """
        project_path = self.resolve_project(to_project)
        inbox_path = os.path.join(project_path, ".orc", to_room, "inbox.json")

        if not os.path.isfile(inbox_path):
            raise ValueError(
                f"Room '{to_room}' not found in project '{to_project}'"
            )

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
