import select
import sys
import termios
import time
import tty

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from orc.service import discover_projects, get_rooms


def build_display():
    """Build the full dashboard display."""
    projects = list(discover_projects().items())

    if not projects:
        return Panel(
            Text("No projects found.", style="dim"),
            title="orc dashboard",
            border_style="blue",
        )

    parts = []
    for project_name, project_root in projects:
        rooms = get_rooms(project_root)

        parts.append(Text(f"■ {project_name}", style="bold cyan"))

        if not rooms:
            parts.append(Text("  No rooms.", style="dim"))
            parts.append(Text(""))
            continue

        table = Table(box=box.SIMPLE, padding=(0, 2), show_edge=False)
        table.add_column("ROOM", style="white", min_width=15)
        table.add_column("ROLE", style="white", min_width=14)
        table.add_column("STATUS", min_width=10)
        table.add_column("TMUX", min_width=6)
        table.add_column("INBOX", justify="right", min_width=5)

        for room in rooms:
            status = room["status"]
            if status == "working":
                status_style = "blue"
            elif status == "blocked":
                status_style = "yellow"
            elif status == "done":
                status_style = "green"
            elif status == "exited":
                status_style = "red"
            else:
                status_style = "dim"

            tmux_text = "alive" if room["tmux"] else "dead"
            tmux_style = "green" if room["tmux"] else "red"

            inbox_count = room["inbox_count"]
            inbox_style = "bold yellow" if inbox_count > 0 else "dim"

            table.add_row(
                Text(room["name"]),
                Text(room["role"]),
                Text(status, style=status_style),
                Text(tmux_text, style=tmux_style),
                Text(str(inbox_count), style=inbox_style),
            )

        parts.append(table)
        parts.append(Text(""))

    return Panel(
        Group(*parts),
        title="orc dashboard",
        subtitle="refreshing 2s · q to quit",
        border_style="blue",
    )


def run_dashboard():
    """Run the live dashboard."""
    console = Console()
    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        with Live(build_display(), console=console, refresh_per_second=1, screen=True) as live:
            last_refresh = time.monotonic()
            while True:
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    ch = sys.stdin.read(1)
                    if ch == "q":
                        break
                now = time.monotonic()
                if now - last_refresh >= 2:
                    live.update(build_display())
                    last_refresh = now
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


if __name__ == "__main__":
    run_dashboard()
