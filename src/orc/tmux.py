import os
import subprocess

ORC_SESSION = "orc"


def _in_tmux():
    return os.environ.get("TMUX") is not None


def session_exists():
    """Check if the orc tmux session exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", ORC_SESSION],
        capture_output=True,
    )
    return result.returncode == 0


def ensure_orc_session():
    """Ensure the main orc tmux session exists."""
    if not session_exists():
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", ORC_SESSION],
            check=True,
            capture_output=True,
        )


def open_window(name, cwd, command=None, background=False):
    """Open a new window in the orc tmux session."""
    ensure_orc_session()
    cmd = [
        "tmux", "new-window", "-t", f"{ORC_SESSION}:", "-n", name,
    ]
    if background:
        cmd.append("-d")
    cmd.extend(["-P", "-F", "#{session_name}:#{window_index}"])
    if cwd:
        cmd.extend(["-c", cwd])
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    target = result.stdout.strip()
    if command:
        subprocess.run(
            ["tmux", "send-keys", "-t", target, command, "Enter"],
            check=True, capture_output=True,
        )


def attach_orc_session():
    """Attach or switch to the orc tmux session."""
    ensure_orc_session()
    if _in_tmux():
        os.execvp("tmux", ["tmux", "switch-client", "-t", ORC_SESSION])
    else:
        os.execvp("tmux", ["tmux", "attach", "-t", ORC_SESSION])


def select_window(name):
    """Select a window by name in the orc session."""
    subprocess.run(
        ["tmux", "select-window", "-t", f"{ORC_SESSION}:{name}"],
        capture_output=True,
    )


def window_exists(name):
    """Check if a window with this name exists in the orc session."""
    result = subprocess.run(
        ["tmux", "list-windows", "-t", ORC_SESSION, "-F", "#{window_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    return name in result.stdout.strip().split("\n")


class RoomSession:
    def __init__(self, project_name, room_name):
        self.room_name = room_name
        self.window_name = f"{project_name}-{room_name.lstrip('@')}"

    def create(self, cwd=None, background=False):
        """Create a window for this room in the orc session."""
        open_window(self.window_name, cwd, background=background)

    def attach(self):
        """Switch to this room's window, or create it if gone."""
        if window_exists(self.window_name):
            select_window(self.window_name)
        else:
            return False
        return True

    def send_keys(self, command):
        """Send a command to this room's window."""
        target = f"{ORC_SESSION}:{self.window_name}"
        # Send text literally, then Enter separately so TUI apps pick it up
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "-l", command],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "Enter"],
            check=True,
            capture_output=True,
        )

    def is_alive(self):
        """Check if this room's window exists."""
        return window_exists(self.window_name)

    def kill(self):
        """Kill this room's window."""
        if self.is_alive():
            subprocess.run(
                ["tmux", "kill-window", "-t", f"{ORC_SESSION}:{self.window_name}"],
                capture_output=True,
            )

    def start_agent(self, backend, role_prompt="", model=None, cwd=None):
        """Start a coding agent in this room's window."""
        cmd = backend.build_command(role_prompt, model=model, cwd=cwd)
        self.send_keys(cmd)
