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
