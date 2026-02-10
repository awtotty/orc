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

- **Universe** (post-MVP):
  - Root directory holding all Orc projects in a system
  - Cross-project agent messaging

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

One tmux session per room:
- `orc add foo` → creates tmux session `orc-foo`, starts Claude Code with role prompt
- `orc foo` → attaches to `tmux session `orc-foo`
- Sessions are independent — one crashing doesn't affect others
- Session naming convention: `orc-{room-name}` (e.g. `orc-main` for @main)

Room lifecycle:
1. `orc add` creates room files, git worktree, tmux session, starts Claude Code, sends role prompt
2. `orc room-name` attaches to existing session
3. If session is dead (status = exited), `orc room-name` recreates session and restarts agent
4. Agent exit is detected and status set to `exited`

## Commands

### Initialize a project

```
orc init
```

Creates `.orc/` directory, `@main` room, and default role files.

### Add a room

```
orc add {room_name} [-r {role}]
```

Creates room directory, git worktree, tmux session, starts agent, sends instruction prompt.
Role defaults to `worker`.

### Open agent chat

```
orc [room]
```

Attaches to the room's tmux session. If no room specified, attaches to `@main`.
If the session is dead (exited), recreates it and restarts the agent.

### Open editor in a room

```
orc edit [room]
```

Opens `$EDITOR` in the room's worktree via tmux.

## Implementation

The engine is written in Python. It is a thin layer that:
- Manages the `.orc/` file structure
- Creates/destroys git worktrees
- Manages tmux sessions (create, attach, detect exit)
- Injects role instructions into Claude Code via `--append-system-prompt`
- Does NOT mediate agent-to-agent communication — agents do that through the filesystem

## Interface

- **orc CLI + tmux**: default UI, ships with orc engine
- **orc.nvim**: optional neovim plugin UI (future)

## Build Plan

Step-by-step implementation order. Each step should be working/testable before moving on.

### Step 1: Python project skeleton

- Set up Python project structure with `pyproject.toml` (use `click` for CLI)
- Entry point: `orc` command
- Basic project layout:
  ```
  src/orc/
    __init__.py
    cli.py          # click CLI entry point
    room.py         # room CRUD (files, worktrees)
    tmux.py         # tmux session management
    roles.py        # role template loading
  ```
- Install as editable: `pip install -e .`
- Verify `orc --help` works

### Step 2: `orc init`

- Find git repo root (walk up from cwd)
- Create `.orc/` directory structure
- Create `@main` room: `agent.json`, `status.json`, `inbox.json`, `molecules/`
- Create `.orc/.roles/orchestrator.md` and `.orc/.roles/worker.md` with initial role prompts
- Add `.orc/.worktrees/` to `.gitignore` (worktrees are local, not committed)
- Error if `.orc/` already exists (or `--force` to reinit)
- Test: run `orc init` in a git repo, verify file structure

### Step 3: `orc add`

- Validate room name (no spaces, no @-prefix except @main)
- Create room directory: `.orc/{room}/agent.json`, `status.json`, `inbox.json`, `molecules/`
- Create git worktree: `git worktree add .orc/.worktrees/{room} -b {room} HEAD`
- Set `agent.json` role (default: worker)
- Set `status.json` to `{"status": "active"}`
- Error if room already exists
- Test: `orc add feature-x`, verify files + worktree created

### Step 4: tmux session management

- `tmux.py`: create session, attach session, check if session exists, kill session
- Session naming: `orc-{room}` (replace `@` with empty, so @main → `orc-main`)
- Create detached session with `tmux new-session -d -s orc-{room} -c {worktree_path}`
- For @main, cwd is repo root
- Send command to session: `tmux send-keys -t orc-{room} '{command}' Enter`
- Attach: `tmux attach -t orc-{room}` (replaces current terminal)
- Check alive: `tmux has-session -t orc-{room}`
- Test: create and attach to a tmux session manually

### Step 5: Wire `orc add` to tmux + Claude Code

- After creating room files + worktree, create tmux session
- Start Claude Code in the session: `claude --append-system-prompt "$(cat .orc/.roles/{role}.md)"`
- cwd = worktree path (or repo root for @main)
- Set status to `active`
- Test: `orc add test-room`, verify tmux session running with Claude Code

### Step 6: `orc [room]` — attach to agent

- If no room specified, default to `@main`
- Check if tmux session `orc-{room}` exists
  - If yes: attach to it
  - If no (exited): recreate session, restart agent, then attach
- Update status on reattach if needed
- Test: `orc test-room` attaches, exit claude, `orc test-room` restarts

### Step 7: `orc edit [room]`

- Open `$EDITOR` in the room's worktree
- If no room specified, default to `@main` (repo root)
- Could open in a new tmux window within the room's session, or a separate session
- Test: `orc edit feature-x` opens editor in worktree

### Step 8: Role prompts (orchestrator.md, worker.md)

- Write the actual role prompt content:
  - Explain the `.orc/` file structure
  - How to read/write inbox.json (mark messages read)
  - How to update status.json
  - How to work with molecules/atoms
  - For orchestrator: how to delegate work, monitor rooms, write to room inboxes
  - For worker: how to check inbox, work on atoms, report back to @main
- These are the seed instructions that make agents "orc-aware"
- Test: start an agent, verify it understands the orc system

### Step 9: Polish and edge cases

- `orc list` or `orc status` — show all rooms with their statuses
- Handle room deletion (`orc rm {room}`)
- Handle already-attached tmux sessions gracefully
- Add `.orc/.worktrees` to project `.gitignore` during init
- Validate that git repo exists before any command
- Helpful error messages throughout
