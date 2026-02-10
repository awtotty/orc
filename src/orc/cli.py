import click
import os
import sys

from orc.project import find_project_root, OrcProject
from orc.roles import _ORC_ROOT

PROJECTS_DIR = os.path.join(_ORC_ROOT, "projects")


@click.group()
def main():
    """orc — orchestrate AI coding agents."""
    pass


def _resolve_project(project_name):
    """Resolve a project name to its root path."""
    path = os.path.join(PROJECTS_DIR, project_name)
    if os.path.isdir(path):
        return path
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
        root = _resolve_project(project)
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
