import click
import sys

from orc.project import find_project_root, OrcProject


@click.group()
def main():
    """orc — orchestrate AI coding agents."""
    pass


def _require_project():
    root = find_project_root()
    if root is None:
        click.echo("Error: not inside a git repository", err=True)
        sys.exit(1)
    project = OrcProject(root)
    if not project.is_initialized():
        click.echo("Error: orc not initialized. Run `orc init` first.", err=True)
        sys.exit(1)
    return project


@main.command()
@click.option("--force", is_flag=True, help="Reinitialize even if .orc/ exists")
def init(force):
    """Initialize orc in the current git repository."""
    root = find_project_root()
    if root is None:
        click.echo("Error: not inside a git repository", err=True)
        sys.exit(1)
    project = OrcProject(root)
    project.init(force=force)
    click.echo(f"Initialized orc in {root}")


@main.command()
@click.argument("room_name")
@click.option("-r", "--role", default="worker", help="Role for the agent (default: worker)")
@click.option("-m", "--message", default=None, help="Initial message to send to the agent")
def add(room_name, role, message):
    """Add a new room with an agent."""
    project = _require_project()
    project.add_room(room_name, role=role, message=message)
    click.echo(f"Created room '{room_name}' with role '{role}'")


@main.command()
@click.argument("room", default="@main")
def attach(room):
    """Attach to a room's tmux session (default: @main)."""
    project = _require_project()
    project.attach(room)


@main.command()
@click.argument("room", default="@main")
def edit(room):
    """Open $EDITOR in a room's worktree."""
    project = _require_project()
    project.edit_room(room)


@main.command(name="list")
def list_rooms():
    """List all rooms and their statuses."""
    project = _require_project()
    project.list_rooms()


@main.command()
@click.argument("room_name")
def rm(room_name):
    """Remove a room."""
    project = _require_project()
    project.remove_room(room_name)
    click.echo(f"Removed room '{room_name}'")
