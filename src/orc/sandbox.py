"""Docker sandbox for the orc infrastructure.

One container named 'orc' that runs the entire orc system —
orc CLI, tmux, and all coding agents. Everything inside works
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


def _needed_backends():
    """Determine which backends to set up in the sandbox.

    Uses sandbox.backends from config if set, otherwise falls back
    to the single default from agent.backend.
    """
    from orc.backend import get_backend
    from orc.config import load as load_config

    cfg = load_config()
    sandbox_cfg = cfg.get("sandbox", {})

    backend_names = sandbox_cfg.get("backends", [])
    if not backend_names:
        agent_cfg = cfg.get("agent", {})
        backend_names = [agent_cfg.get("backend", "claude")]

    backends = []
    seen = set()
    for name in backend_names:
        if name in seen:
            continue
        seen.add(name)
        b = get_backend(name)
        if b:
            backends.append(b)

    return backends


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
        cfg = {"ports": ["7777:7777"], "packages": [], "mounts": [], "env": [], "backends": []}

    backends = _needed_backends()

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
        # Proper init process (tini) so signals work (needed for shutdown)
        "--init",
        # Stable hostname so auth tokens persist across restarts
        "--hostname", CONTAINER_NAME,
        # Mount orc root at same absolute path (project + worktrees + git refs all work)
        "-v", f"{orc_root}:{orc_root}",
        # Set HOME so ~ resolves correctly
        "-e", f"HOME={home}",
        # Terminal support
        "-e", "TERM=xterm-256color",
        # Signal to orc that we're inside the sandbox
        "-e", "ORC_SANDBOX=1",
        # Tell orc where the source root is (installed package can't derive it from __file__)
        "-e", f"ORC_ROOT={orc_root}",
        "-w", orc_root,
    ]

    # Backend-specific mounts
    for b in backends:
        if b.sandbox_mounts:
            for mount in b.sandbox_mounts():
                run_cmd.extend(["-v", mount])

    # Config-driven port forwarding
    for port in cfg["ports"]:
        run_cmd.extend(["-p", port])

    # Config-driven extra mounts
    for mount in cfg["mounts"]:
        run_cmd.extend(["-v", mount])

    # Config-driven extra env vars
    for env in cfg["env"]:
        run_cmd.extend(["-e", env])

    # Editor (used by `orc edit` and other tools)
    run_cmd.extend(["-e", f"EDITOR={cfg['editor']}"])

    # User config (tmux, nvim, etc.)
    configdir = os.path.join(home, ".config")
    if os.path.isdir(configdir):
        run_cmd.extend(["-v", f"{configdir}:{configdir}:ro"])

    # lazy.nvim lockfile needs to be writable (override the ro config mount)
    lazy_lock = os.path.join(home, ".config", "nvim", "lazy-lock.json")
    if os.path.exists(lazy_lock):
        run_cmd.extend(["-v", f"{lazy_lock}:{lazy_lock}"])

    # Neovim plugins (lazy.nvim installs here on host)
    nvim_data = os.path.join(home, ".local", "share", "nvim")
    if os.path.isdir(nvim_data):
        run_cmd.extend(["-v", f"{nvim_data}:{nvim_data}"])

    # Git config (optional)
    gitconfig = os.path.join(home, ".gitconfig")
    if os.path.exists(gitconfig):
        run_cmd.extend(["-v", f"{gitconfig}:{gitconfig}:ro"])

    # SSH agent forwarding (optional)
    ssh_sock = os.environ.get("SSH_AUTH_SOCK")
    if ssh_sock:
        run_cmd.extend(["-v", f"{ssh_sock}:{ssh_sock}", "-e", f"SSH_AUTH_SOCK={ssh_sock}"])

    # GitHub token (gh uses keyring on host, pass token via env for container)
    gh_token = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True,
    )
    if gh_token.returncode == 0 and gh_token.stdout.strip():
        run_cmd.extend(["-e", f"GH_TOKEN={gh_token.stdout.strip()}"])

    # Backend-specific env vars (deduplicated across backends)
    seen_env = set()
    for b in backends:
        for var_name in b.sandbox_env_vars:
            if var_name in seen_env:
                continue
            seen_env.add(var_name)
            val = os.environ.get(var_name)
            if val:
                run_cmd.extend(["-e", f"{var_name}={val}"])

    run_cmd.append(IMAGE_NAME)

    click.echo("Starting sandbox...")
    subprocess.run(run_cmd, check=True)

    # Ensure HOME, ~/.local/bin, and ~/.cache exist
    uid_gid = f"{os.getuid()}:{os.getgid()}"
    subprocess.run(
        ["docker", "exec", "-u", "0", CONTAINER_NAME,
         "bash", "-c",
         f"mkdir -p {home}/.local/bin {home}/.local/share {home}/.cache"
         f" && chown {uid_gid} {home} {home}/.local {home}/.local/bin {home}/.local/share {home}/.cache"],
        check=True,
    )

    # Set up gh as git credential helper so git push works
    subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "gh", "auth", "setup-git"],
        capture_output=True,
    )

    # Backend-specific post-start setup
    for b in backends:
        if b.sandbox_setup:
            click.echo(f"Setting up {b.name} backend...")
            try:
                b.sandbox_setup(CONTAINER_NAME, home)
            except subprocess.CalledProcessError as e:
                click.echo(f"Warning: {b.name} backend setup failed: {e}", err=True)

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


def init():
    """Initialize tmux session and dashboard inside the sandbox (non-interactive)."""
    if not _is_running():
        click.echo("Sandbox is not running. Run `orc sandbox start` first.", err=True)
        sys.exit(1)
    subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "orc", "_tmux-setup"],
        check=True,
    )


def attach():
    """Attach to the sandbox container with tmux (bash + dash)."""
    if not _is_running():
        click.echo("Sandbox is not running. Run `orc sandbox start` first.", err=True)
        sys.exit(1)
    os.execvp("docker", ["docker", "exec", "-it", CONTAINER_NAME, "orc", "_tmux-init"])
