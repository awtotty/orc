"""Docker sandbox for the orc infrastructure.

One container named 'orc' that runs the entire orc system —
orc CLI, tmux, and all claude agents. Everything inside works
exactly as it does on the host.
"""

import os
import subprocess
import sys

import click

IMAGE_NAME = "orc-sandbox"
CONTAINER_NAME = "orc"


def _orc_root():
    """Return the root of the orc source tree (where pyproject.toml lives)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _dockerfile_dir():
    return os.path.join(_orc_root(), "container")


def _is_running():
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def start():
    """Build the image and start the sandbox container."""
    if _is_running():
        click.echo("Sandbox is already running.")
        return

    try:
        from orc.config import load as load_config
        cfg = load_config()["sandbox"]
    except Exception as e:
        click.echo(f"Warning: failed to load config ({e}), using defaults.", err=True)
        cfg = {"ports": ["7777:7777"], "packages": [], "mounts": [], "env": []}

    dockerfile_dir = _dockerfile_dir()
    if not os.path.isdir(dockerfile_dir):
        click.echo(f"Error: container/ not found at {dockerfile_dir}", err=True)
        sys.exit(1)

    click.echo("Building sandbox image...")
    subprocess.run([
        "docker", "build",
        "--build-arg", f"UID={os.getuid()}",
        "--build-arg", f"GID={os.getgid()}",
        "-t", IMAGE_NAME, dockerfile_dir,
    ], check=True)

    # Remove any stopped container with the same name
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)

    orc_root = _orc_root()
    home = os.path.expanduser("~")

    run_cmd = [
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        # Mount orc root at same absolute path (project + worktrees + git refs all work)
        "-v", f"{orc_root}:{orc_root}",
        # Mount Claude credentials for OAuth
        "-v", f"{home}/.claude:{home}/.claude",
        # Set HOME so ~ resolves correctly
        "-e", f"HOME={home}",
        # Terminal support (Claude Code needs this)
        "-e", "TERM=xterm-256color",
        # Signal to orc that we're inside the sandbox
        "-e", "ORC_SANDBOX=1",
        # Tell orc where the source root is (installed package can't derive it from __file__)
        "-e", f"ORC_ROOT={orc_root}",
        "-w", orc_root,
    ]

    # Config-driven port forwarding
    for port in cfg["ports"]:
        run_cmd.extend(["-p", port])

    # Config-driven extra mounts
    for mount in cfg["mounts"]:
        run_cmd.extend(["-v", mount])

    # Config-driven extra env vars
    for env in cfg["env"]:
        run_cmd.extend(["-e", env])

    # User config (tmux, nvim, etc.)
    configdir = os.path.join(home, ".config")
    if os.path.isdir(configdir):
        run_cmd.extend(["-v", f"{configdir}:{configdir}:ro"])

    # Git config (optional)
    gitconfig = os.path.join(home, ".gitconfig")
    if os.path.exists(gitconfig):
        run_cmd.extend(["-v", f"{gitconfig}:{gitconfig}:ro"])

    # SSH agent forwarding (optional)
    ssh_sock = os.environ.get("SSH_AUTH_SOCK")
    if ssh_sock:
        run_cmd.extend(["-v", f"{ssh_sock}:{ssh_sock}", "-e", f"SSH_AUTH_SOCK={ssh_sock}"])

    # API key (optional, OAuth is preferred)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        run_cmd.extend(["-e", f"ANTHROPIC_API_KEY={api_key}"])

    run_cmd.append(IMAGE_NAME)

    click.echo("Starting sandbox...")
    subprocess.run(run_cmd, check=True)

    # Ensure HOME and ~/.local/bin exist, and symlink claude there
    # (Claude Code's config in ~/.claude records installMethod=native expecting ~/.local/bin/claude)
    uid_gid = f"{os.getuid()}:{os.getgid()}"
    subprocess.run(
        ["docker", "exec", "-u", "0", CONTAINER_NAME,
         "bash", "-c",
         f"mkdir -p {home}/.local/bin"
         f" && chown {uid_gid} {home} {home}/.local {home}/.local/bin"
         f" && ln -sf /usr/local/bin/claude {home}/.local/bin/claude"],
        check=True,
    )

    # Install orc inside the container (as root for system pip access)
    click.echo("Installing orc inside sandbox...")
    subprocess.run(
        ["docker", "exec", "-u", "0", CONTAINER_NAME,
         "uv", "pip", "install", "--system", orc_root],
        check=True,
    )

    # Install extra packages from config
    if cfg["packages"]:
        click.echo(f"Installing extra packages: {', '.join(cfg['packages'])}...")
        subprocess.run(
            ["docker", "exec", "-u", "0", CONTAINER_NAME,
             "apt-get", "install", "-y", "-qq"] + cfg["packages"],
            check=True,
        )

    click.echo("Sandbox is running. Use `orc sandbox attach` to enter.")


def stop():
    """Stop and remove the sandbox container."""
    if not _is_running():
        click.echo("Sandbox is not running.")
        return
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)
    click.echo("Sandbox stopped.")


def status():
    """Show sandbox status."""
    if _is_running():
        click.echo("Sandbox: running")
    else:
        click.echo("Sandbox: stopped")


def attach():
    """Attach to the sandbox container and launch orc."""
    if not _is_running():
        click.echo("Sandbox is not running. Run `orc sandbox start` first.", err=True)
        sys.exit(1)
    os.execvp("docker", ["docker", "exec", "-it", CONTAINER_NAME, "orc", "attach"])
