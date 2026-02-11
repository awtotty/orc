# orc

CLI for orchestrating AI coding agents. orc manages the environment — git worktrees, tmux sessions, role instructions — and Claude Code does the actual work. Agents communicate through the filesystem via the `.orc/` directory.

## Install

```sh
git clone https://github.com/awtotty/orc.git
cd orc
uv tool install -e .
```

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+. Docker is not required but highly recommended.

## Quick start

```sh
orc start
```

This builds a Docker sandbox with all dependencies (git, tmux, Claude Code, etc.), starts it, and drops you into the orc tmux session. Everything runs inside the container — no host setup beyond Docker is needed. Claude agents run with `dangerously-skip-permissions` by default.

When you're done:

```sh
orc stop
```

### Inside the sandbox

Set up a project and start working:

```sh
cd ~/orc/projects
git clone https://github.com/you/your-repo.git
cd your-repo
orc init       # creates .orc/ directory with orchestrator room
orc attach     # attach to the @main orchestrator
```

Tell the orchestrator what you want. Tell it to delegate, and it will. Tell it to clean up, and it will. Tell it to do something on its own, and it will.

## How it works

Each room is a workspace isolated at the git level (separate worktree and branch), not at the container level — all rooms share the same sandbox:

- **@main** lives at the project root and runs the orchestrator role
- **Worker rooms** each get their own git worktree (branch) and tmux window
- Agents communicate by reading/writing JSON files in `.orc/` (inboxes, statuses, molecules)
- Role prompts in `.orc/.roles/` teach agents how to use the orc system

You navigate it all with the help of tmux.

## Roles

Roles live in the `roles/` directory as markdown files:

- `roles/system.md` — orc system instructions (injected into every agent)
- `roles/orchestrator.md` — orchestrator-specific instructions
- `roles/worker.md` — worker-specific instructions
- `roles/merger.md` — merge conflict resolver instructions

Each agent gets `system.md` + their role file as a combined system prompt.

## Commands

### `orc start`

Builds the sandbox image (if needed), starts the container, and attaches. This is the main entry point — run it to get going.

### `orc stop`

Stops and removes the sandbox container.

### `orc init`

Initializes orc in a git repository. Creates `.orc/` with the `@main` orchestrator room and default role files.

### `orc add <room> [-r role] [-m message]`

Creates a worker room with its own git worktree, tmux window, and Claude Code agent.

```sh
orc add feature-auth
orc add bug-fix -r worker
orc add feature-auth -m "implement the auth module"
```

### `orc attach [room]`

Attaches to a room's tmux session. Defaults to `@main`. Restarts the agent if the session died.

### `orc edit [room]`

Opens `$EDITOR` in a room's working directory.

### `orc list`

Shows all rooms with their role, status, and whether the tmux session is alive.

### `orc rm <room>`

Kills the tmux session, removes the git worktree, and deletes the room's files.

### `orc send <room> -m <message>`

Sends a message to a room's inbox.

### `orc tell <room> -m <message>`

Sends a message directly to a running agent's Claude Code session.

## Sandbox

The sandbox is a Docker container that provides a fully isolated environment with everything pre-installed: git, tmux, Claude Code, GitHub CLI, Node.js, Python, and more.

`orc start` and `orc stop` are the primary interface. Under the hood, these use `orc sandbox start/stop/status/attach`.

You can run orc commands ouside of the sandbox as much as you like, though agents won't have `dangerously-skip-permissions` enabled by default. That said, it's probably better to run orc in the sandbox for the isolation benefits.

### What gets mounted

- Your project directory (at the same absolute path)
- `~/.claude` (Claude Code OAuth credentials)
- `~/.config` (git credential helper, gh auth, tmux/nvim config) — read-only
- `~/.gitconfig` — read-only
- SSH agent socket (if available)

## Configuration

Create a `config.toml` in the orc source root to customize the sandbox and other behavior:

```toml
[sandbox]
ports = ["7777:7777", "3000:3000"]
packages = ["postgresql-client"]
mounts = ["/host/path:/container/path"]
env = ["MY_VAR=value"]
```

## Creating a custom role

Add a markdown file to `roles/`:

```sh
echo "# Reviewer\n\nYou review pull requests..." > roles/reviewer.md
```

Then use it when adding a room:

```sh
orc add code-review -r reviewer
```

The file name (minus `.md`) is the role name. No code changes needed.
