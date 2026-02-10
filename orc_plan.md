## Overview

The original version of this software was a neovim plugin (orc.nvim).
We are rebuilding it as a general purpose CLI for orchestrating AI coding agents.

The core idea: orc manages the environment (worktrees, file structure, role instructions)
and Claude Code does the actual work. Agents communicate through the filesystem — the `.orc/`
directory is both orc's state store and the agents' communication medium.

## MVP Scope

- Single repo (no Universe/cross-project messaging yet)
- Rooms + Agents + Roles
- File-based state in `.orc/`
- Python CLI that uses tmux to run agents and editors
- Atoms with status tracking
- Bidirectional communication: orchestrator → room inboxes, agents → @main inbox

## Data Model

- **Room** (formerly "space"):
  - git worktree (except @main which uses the repo root)
  - agent (Claude Code instance with role instructions)
  - inbox (messages from other agents)
  - status (active, ready, blocked, done, exited)
  - molecules (collections of atoms)

- **Agent**:
  - A Claude Code instance with injected role instructions
  - Role injected via `--append-system-prompt` (repos already have their own CLAUDE.md)
  - Sessions are ephemeral Claude Code session IDs
  - Agents self-direct but follow orchestrator orders

- **Inbox**:
  - Array of messages from other agents
  - Messages are marked as read (not deleted) — cleanup handled separately
  - Format:
    ```json
    [
      {"from": "@main", "message": "implement auth module", "read": false, "ts": "..."},
      {"from": "room-x", "message": "API contract ready", "read": true, "ts": "..."}
    ]
    ```

- **Molecule**:
  - Collection of Atoms forming a dependency graph

- **Atom**:
  - JSON with information for agents (work items, context, etc.)
  - Tracks status: `todo` → `in_progress` → `done`
  - Can have dependencies on other Atoms

- **Project**:
  - A repo with a `.orc/` directory
  - Lives under `orc/projects/` for Universe support

- **Universe**:
  - The `orc/projects/` directory holding all orc projects
  - Cross-project agent messaging
  - CLI: `orc projects`, `orc project-add`, `orc project-rm`, `orc send`

## @main

`@main` is a special Room:
- Lives at the project root (not a worktree)
- Agent gets the orchestrator role
- Inbox contains project-wide information
- Orchestrator breaks down work, delegates via other rooms' inboxes, monitors statuses
- Can poll room statuses as needed; agents also message @main inbox proactively

## Key Roles

- **Orchestrator**: breaks down work, delegates to agents, monitors room statuses, manages @main
- **Worker**: completes tasks defined in its molecules, reports back to orchestrator
- **Merger**: resolves merge conflicts (may not be needed if orchestrator handles this)

Role definitions live in `.orc/.roles/` as markdown files containing the system prompt
instructions for that role (how to use the orc system, read inbox, update status, etc.)

## File Structure

After `orc init`:

```
project-root/
├── source files...
└── .orc/
    ├── @main/
    │   ├── agent.json        # {"role": "orchestrator", "sessions": []}
    │   ├── status.json       # {"status": "ready"}
    │   ├── inbox.json        # []
    │   └── molecules/
    ├── .roles/
    │   ├── orchestrator.md   # system prompt for orchestrator
    │   └── worker.md         # system prompt for worker
    ├── .worktrees/           # git worktrees (gitignored)
    └── ... (rooms added later)
```

After `orc add feature-x -r worker`:

```
.orc/
├── @main/
│   └── ...
├── feature-x/
│   ├── agent.json            # {"role": "worker", "sessions": []}
│   ├── status.json           # {"status": "active"}
│   ├── inbox.json            # []
│   └── molecules/
├── .roles/
│   └── ...
└── .worktrees/
    └── feature-x/            # git worktree on branch feature-x
        └── project source files...
```

## tmux Architecture

One `orc` tmux session with windows for each room:
- `orc add foo` → creates window `{project}-foo` in the `orc` session, starts Claude Code
- `orc attach foo` → selects the window in the `orc` session
- `orc edit foo` → creates window `{project}-foo-edit` with `$EDITOR`
- Windows are named `{project_dir}-{room}` to avoid collisions across projects
- If already inside tmux, uses `switch-client`; otherwise uses `attach`

Room lifecycle:
1. `orc add` creates room files, git worktree, tmux window, starts Claude Code with role prompt
2. `orc attach room` selects existing window
3. If window is dead, `orc attach room` recreates window and restarts agent
4. `orc rm room` kills window, removes worktree, deletes room files

## Commands

### Initialize a project

```
orc init [--force]
```

Creates `.orc/` directory, `@main` room, default role files, `.worktrees/` directory.
Adds `.orc/.worktrees/` to `.gitignore`. Errors if `.orc/` already exists unless `--force`.

### Add a room

```
orc add {room_name} [-r {role}] [-m {message}]
```

Creates room directory, git worktree, tmux window, starts Claude Code with role prompt.
Role defaults to `worker`. Use `-m` to send an initial message to the agent.

### Attach to a room

```
orc attach [room]
```

Selects the room's tmux window. Defaults to `@main`.
If the window is dead, recreates it and restarts the agent.

### Open editor in a room

```
orc edit [room]
```

Opens `$EDITOR` in a new tmux window in the room's worktree. Defaults to `@main`.

### List rooms

```
orc list
```

Shows all rooms with their role, status, and tmux window state.

### Remove a room

```
orc rm {room_name}
```

Kills tmux window, removes git worktree, deletes room files. Cannot remove `@main`.

## Implementation

The engine is written in Python. It is a thin layer that:
- Manages the `.orc/` file structure
- Creates/destroys git worktrees
- Manages tmux windows within a single `orc` session
- Injects role instructions into Claude Code via `--append-system-prompt`
- Does NOT mediate agent-to-agent communication — agents do that through the filesystem

### Project layout

```
src/orc/
  __init__.py
  cli.py          # click CLI entry point
  project.py      # project-level operations (init, add, attach, edit, list, rm)
  room.py         # room CRUD (agent.json, status.json, inbox.json, molecules/)
  tmux.py         # tmux session/window management
  roles.py        # role template loading (orchestrator.md, worker.md)
```

### Install

```
uv tool install -e .
```

Installs `orc` globally via uv. Editable mode — source changes take effect immediately.

## Interface

- **orc CLI + tmux**: default UI, ships with orc engine
- **orc.nvim**: optional neovim plugin UI (future)

## Build Plan (completed)

### Step 1: Python project skeleton ✓
### Step 2: `orc init` ✓
### Step 3: `orc add` ✓
### Step 4: tmux session management ✓
### Step 5: Wire `orc add` to tmux + Claude Code ✓
### Step 6: `orc attach` ✓
### Step 7: `orc edit` ✓
### Step 8: Role prompts (orchestrator.md, worker.md) ✓
### Step 9: Polish and edge cases ✓
