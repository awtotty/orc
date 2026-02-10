import os
import subprocess


class TmuxSession:
    def __init__(self, room_name):
        self.room_name = room_name
        # @main → orc-main, feature-x → orc-feature-x
        self.session_name = "orc-" + room_name.lstrip("@")

    def create(self, cwd=None):
        """Create a detached tmux session."""
        cmd = ["tmux", "new-session", "-d", "-s", self.session_name]
        if cwd:
            cmd.extend(["-c", cwd])
        subprocess.run(cmd, check=True, capture_output=True)

    def attach(self):
        """Attach to the tmux session (replaces current process)."""
        os.execvp("tmux", ["tmux", "attach", "-t", self.session_name])

    def send_keys(self, command):
        """Send a command to the tmux session."""
        subprocess.run(
            ["tmux", "send-keys", "-t", self.session_name, command, "Enter"],
            check=True,
            capture_output=True,
        )

    def is_alive(self):
        """Check if the tmux session exists."""
        result = subprocess.run(
            ["tmux", "has-session", "-t", self.session_name],
            capture_output=True,
        )
        return result.returncode == 0

    def kill(self):
        """Kill the tmux session if it exists."""
        if self.is_alive():
            subprocess.run(
                ["tmux", "kill-session", "-t", self.session_name],
                capture_output=True,
            )

    def start_claude(self, role_prompt, message=None):
        """Start Claude Code in the session with the role prompt."""
        cmd = "claude"
        if role_prompt:
            # Escape single quotes in the prompt for shell
            escaped = role_prompt.replace("'", "'\\''")
            cmd += f" --append-system-prompt $'{escaped}'"
        if message:
            escaped_msg = message.replace("'", "'\\''")
            cmd += f" --yes -m $'{escaped_msg}'"
        self.send_keys(cmd)
