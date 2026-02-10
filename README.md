# orc

CLI for orchestrating AI coding agents. orc manages the environment — git worktrees, tmux sessions, role instructions — and Claude Code does the actual work. Agents communicate through the filesystem via the `.orc/` directory.

## Requirements

- Python 3.11+
- git
- tmux
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)

## Install

```sh
git clone https://github.com/awtotty/orc.git
cd orc
uv tool install -e .
```

## Usage

### Set up a project

Clone your repo into `~/orc/projects/` and initialize it:

```sh
git clone https://github.com/you/your-repo.git ~/orc/projects/your-repo
cd ~/orc/projects/your-repo
orc init
```

Creates a `.orc/` directory with the `@main` orchestrator room and default role files.

### Add a worker room

```sh
orc add feature-auth
orc add bug-fix -r worker
orc add feature-auth -m "implement the auth module"
```

Creates a git worktree, tmux session, and starts Claude Code with the role's system prompt. Use `-m` to send an initial message to the agent.

### Attach to a room

```sh
orc attach           # attach to @main
orc attach feature-auth
```

Opens the room's tmux session. If the session died, it restarts the agent automatically.

### Open an editor in a room's worktree

```sh
orc edit             # edit @main (project root)
orc edit feature-auth
```

Opens `$EDITOR` in the room's working directory.

### List rooms

```sh
orc list
```

Shows all rooms with their role, status, and whether the tmux session is alive.

### Remove a room

```sh
orc rm feature-auth
```

Kills the tmux session, removes the git worktree, and deletes the room's files.

## How it works

Each room is an isolated workspace:

- **@main** lives at the project root and runs the orchestrator role
- **Worker rooms** each get their own git worktree (branch) and tmux session
- Agents communicate by reading/writing JSON files in `.orc/` (inboxes, statuses, molecules)
- Role prompts in `.orc/.roles/` teach agents how to use the orc system

## Project structure

orc expects projects to live under `~/orc/projects/`:

```
~/orc/
├── src/orc/            # orc source
├── projects/           # your repos (gitignored)
│   ├── my-app/
│   │   └── .orc/
│   └── my-lib/
│       └── .orc/
```

This layout supports cross-project agent messaging (Universe) in the future.
