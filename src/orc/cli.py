import click
import os
import sys

from orc.project import find_project_root, OrcProject
from orc.universe import Universe, PROJECTS_DIR


@click.group()
def main():
    """orc — orchestrate AI coding agents."""
    pass


def _resolve_project(project_name):
    """Resolve a project name to its root path."""
    uni = Universe()
    try:
        return uni.resolve_project(project_name)
    except ValueError:
        click.echo(f"Error: project '{project_name}' not found in {PROJECTS_DIR}", err=True)
        sys.exit(1)


def _require_project(project_name=None):
    if project_name:
        root = _resolve_project(project_name)
    else:
        root = find_project_root()
        if root is None:
            click.echo("Error: not inside a git repository. Use -p to specify a project.", err=True)
            sys.exit(1)
    project = OrcProject(root)
    if not project.is_initialized():
        click.echo("Error: orc not initialized. Run `orc init` first.", err=True)
        sys.exit(1)
    return project


@main.command()
@click.option("--force", is_flag=True, help="Reinitialize even if .orc/ exists")
@click.option("-p", "--project", default=None, help="Project name in projects/")
def init(force, project):
    """Initialize orc in a git repository."""
    if project:
        # For init, the project might not be initialized yet — just resolve the path
        uni = Universe()
        all_projs = uni.all_projects()
        if project in all_projs:
            root = all_projs[project]
        else:
            click.echo(f"Error: project '{project}' not found in {PROJECTS_DIR}", err=True)
            sys.exit(1)
    else:
        root = find_project_root()
        if root is None:
            click.echo("Error: not inside a git repository. Use -p to specify a project.", err=True)
            sys.exit(1)
    proj = OrcProject(root)
    proj.init(force=force)
    click.echo(f"Initialized orc in {root}")


@main.command()
@click.argument("room_name")
@click.option("-r", "--role", default="worker", help="Role for the agent (default: worker)")
@click.option("-m", "--message", default=None, help="Initial message to send to the agent")
@click.option("-p", "--project", default=None, help="Project name in projects/")
def add(room_name, role, message, project):
    """Add a new room with an agent."""
    proj = _require_project(project)
    proj.add_room(room_name, role=role, message=message)
    click.echo(f"Created room '{room_name}' with role '{role}'")


@main.command()
@click.argument("room", default="@main")
@click.option("-p", "--project", default=None, help="Project name in projects/")
def attach(room, project):
    """Attach to a room's tmux session (default: @main)."""
    proj = _require_project(project)
    proj.attach(room)


@main.command()
@click.argument("room", default="@main")
@click.option("-p", "--project", default=None, help="Project name in projects/")
def edit(room, project):
    """Open $EDITOR in a room's worktree."""
    proj = _require_project(project)
    proj.edit_room(room)


@main.command(name="list")
@click.option("-p", "--project", default=None, help="Project name in projects/")
def list_rooms(project):
    """List all rooms and their statuses."""
    proj = _require_project(project)
    proj.list_rooms()


@main.command()
@click.argument("room_name")
@click.option("-p", "--project", default=None, help="Project name in projects/")
def rm(room_name, project):
    """Remove a room."""
    proj = _require_project(project)
    proj.remove_room(room_name)
    click.echo(f"Removed room '{room_name}'")


@main.command()
@click.option("-p", "--project", default=None, help="Project name in projects/")
def clean(project):
    """Clean up dead resources (read messages, completed molecules)."""
    proj = _require_project(project)
    messages, molecules = proj.clean()
    if messages or molecules:
        parts = []
        if messages:
            parts.append(f"{messages} read message{'s' if messages != 1 else ''}")
        if molecules:
            parts.append(f"{molecules} completed molecule{'s' if molecules != 1 else ''}")
        click.echo(f"Cleaned {', '.join(parts)}")
    else:
        click.echo("Nothing to clean")


# ---------------------------------------------------------------------------
# Universe commands
# ---------------------------------------------------------------------------


@main.command()
def projects():
    """List all projects in the universe."""
    uni = Universe()
    all_projs = uni.all_projects()

    if not all_projs:
        click.echo(f"No projects found in {PROJECTS_DIR}")
        return

    initialized = uni.discover()

    click.echo(f"{'PROJECT':<25} {'STATUS':<15} {'PATH'}")
    click.echo("-" * 75)
    for name, path in all_projs.items():
        status = "initialized" if name in initialized else "not initialized"
        symlink = " -> " + os.readlink(os.path.join(PROJECTS_DIR, name)) if os.path.islink(os.path.join(PROJECTS_DIR, name)) else ""
        click.echo(f"{name:<25} {status:<15} {path}{symlink}")


@main.command(name="project-add")
@click.argument("path")
@click.option("-n", "--name", default=None, help="Name for the project (default: directory name)")
def project_add(path, name):
    """Register a project in the universe."""
    uni = Universe()
    try:
        registered_name = uni.add_project(path, name=name)
        click.echo(f"Added project '{registered_name}' -> {os.path.realpath(path)}")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command(name="project-rm")
@click.argument("name")
def project_rm(name):
    """Remove a project from the universe."""
    uni = Universe()
    try:
        uni.remove_project(name)
        click.echo(f"Removed project '{name}' from universe")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
@click.argument("target")
@click.option("-m", "--message", required=True, help="Message to send")
@click.option("-f", "--from-addr", "from_addr", default="cli", help="Sender address (default: cli)")
@click.option("-p", "--project", default=None, help="Project context (for local sends)")
def send(target, message, from_addr, project):
    """Send a message to a room. Target: 'room' (local) or 'project/room' (cross-project)."""
    if "/" in target:
        # Cross-project: project/room
        to_project, to_room = target.split("/", 1)
        uni = Universe()
        try:
            uni.send_message(from_addr, to_project, to_room, message)
            click.echo(f"Sent to {to_project}/{to_room}")
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
    else:
        # Local: just a room name within the current/specified project
        proj = _require_project(project)
        to_room = target
        inbox_path = os.path.join(proj.orc_dir, to_room, "inbox.json")
        if not os.path.isfile(inbox_path):
            click.echo(f"Error: room '{to_room}' not found", err=True)
            sys.exit(1)

        import json
        from datetime import datetime, timezone

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
        click.echo(f"Sent to {to_room}")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@main.command()
@click.option("--port", default=7777, type=int, help="Port to listen on (default: 7777)")
def dash(port):
    """Open the orc web dashboard."""
    from orc.tmux import open_window, window_exists
    import webbrowser

    name = ".orc-dash"
    if window_exists(name):
        click.echo("orc dash is already running.")
    else:
        open_window(name, os.getcwd(), f"orc _dash-server --port {port}")
        click.echo(f"orc dashboard -> http://localhost:{port}")
    webbrowser.open(f"http://localhost:{port}")


@main.command(name="_dash-server", hidden=True)
@click.option("--port", default=7777, type=int)
def dash_server(port):
    """Internal: run the web server directly."""
    from orc.web import run_server
    run_server(port=port)
