# Orc (Orchestrator) — Neovim Plugin

A neovim plugin for orchestrating AI coding agents across git worktrees.

## Core Concept

Manage multiple Claude Code (or other CLI) sessions, each isolated in its own git worktree. Prompt agents from any buffer with optional line selection for context. Get notified when agents need attention.

## MVP Features

### 1. Space Management (`lua/orc/spaces.lua`)

A "space" is a git worktree + a persistent hidden terminal buffer running Claude Code.

- **State**: Table mapping space name → `{bufnr, worktree_path, branch, status}`
- **Create space**: `git worktree add` a new branch, open a terminal buffer with `jobstart` running `claude` in that worktree, hide the buffer
- **Toggle space**: Show/hide a space's terminal in a split or float
- **Delete space**: Kill the terminal job, `git worktree remove`, clean up state
- **List spaces**: Show all spaces with their status (active, needs attention, etc.)

### 2. Prompt with Context (`lua/orc/prompt.lua`)

Send prompts to any space's terminal, optionally with selected code as reference.

- **Visual select → prompt**: Grab selection, format as fenced code block with filename and line range, open a small prompt input (float or cmdline), concatenate context + user prompt, send to the target space's terminal via `chansend()`
- **Prompt without selection**: Just open prompt input and send to active space
- **Format**:
  ```
  In `src/foo.rs:12-24`:
  ```rust
  <selected code>
  ```

  <user's prompt>
  ```

### 3. Signal File Notifications (`lua/orc/signal.lua`)

Convention-based notification system. Claude is instructed to write to `.claude/signal` in its worktree when it needs human input or finishes a task.

- **File watching**: Use `vim.uv.new_fs_event()` to watch each worktree's signal file
- **Signal format**: Simple line-based, e.g. `DONE: implemented the feature`, `QUESTION: should I use X or Y?`, `BLOCKED: tests failing`
- **Notification display**: `vim.notify()` with the signal content, linking to the space

## File Structure

```
orc/
├── lua/
│   └── orc/
│       ├── init.lua       -- Setup, config defaults, public API
│       ├── spaces.lua     -- Worktree CRUD, terminal lifecycle, state
│       ├── prompt.lua     -- Visual selection capture, prompt input, send
│       └── signal.lua     -- File watchers, notification routing
└── plugin/
    └── orc.lua            -- User commands and default keymaps
```

## Commands

- `:OrcCreate <name>` — Create a new space (worktree + branch + terminal)
- `:OrcToggle [name]` — Toggle the terminal for a space (default: current/last active)
- `:OrcDelete <name>` — Tear down a space
- `:OrcList` — Show all spaces and their status
- `:OrcPrompt [name]` — Open prompt input for a space (works with visual selection)
- `:OrcSwitch <name>` — Set the active space for prompts

## Dependencies

- Neovim >= 0.10
- git (with worktree support)
- A CLI agent (claude, codex, etc.) — configurable

## Config

```lua
require("orc").setup({
  cli = "claude",           -- CLI command to run in each space
  worktree_base = "../orc-spaces", -- Where to put worktrees (relative to repo root)
  signal_file = ".claude/signal",  -- Signal file path within worktree
  terminal_direction = "float",    -- "float", "vertical", "horizontal"
})
```

## Build Order

1. `spaces.lua` — Get worktree + terminal lifecycle working
2. `plugin/orc.lua` + `init.lua` — Wire up commands and config
3. `prompt.lua` — Visual select → prompt → send
4. `signal.lua` — File watching and notifications
