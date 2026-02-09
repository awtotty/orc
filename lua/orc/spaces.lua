local M = {}

---@class OrcSpace
---@field bufnr number Terminal buffer number
---@field chan number Terminal channel ID
---@field worktree_path string Absolute path to the worktree
---@field branch string Branch name
---@field status string "active"|"ready"|"needs_attention"|"exited"
---@field win number|nil Window ID if currently visible
---@field last_file string|nil
---@field last_cursor number[]|nil  -- {line, col}

---@type table<string, OrcSpace>
M.spaces = {}

---@type string|nil
M.active_space = nil

---@type string|nil  -- last_file for @main when it hasn't been spawned yet
M._main_last_file = nil
---@type number[]|nil  -- last_cursor for @main when it hasn't been spawned yet
M._main_last_cursor = nil

local function get_config()
  return require("orc").config
end

local function repo_root()
  local out = vim.fn.systemlist("git rev-parse --show-toplevel")
  if vim.v.shell_error ~= 0 then
    return nil
  end
  return out[1]
end

--- Ensure the shared CLAUDE.md exists in the worktree base directory.
--- Claude Code walks parent directories, so all worktrees inherit it.
---@param base_dir string The .orc directory
local function ensure_signal_instructions(base_dir)
  vim.fn.mkdir(base_dir, "p")
  local claude_md = base_dir .. "/CLAUDE.md"

  -- Overwrite each time so instruction updates take effect
  local f

  f = io.open(claude_md, "w")
  if f then
    f:write([[# Orc Signal Protocol

You are running in an Orc space. The user cannot see your output unless they open your terminal. You MUST signal the editor so the user knows when you need them.

Signal by appending to `$ORC_SIGNAL_FILE`:

```
echo "TYPE: message" >> "$ORC_SIGNAL_FILE"
```

IMPORTANT: Always signal BEFORE stopping or waiting. The user will not see your question otherwise.

- BEFORE asking the user anything: `echo "QUESTION: <your question>" >> "$ORC_SIGNAL_FILE"`
- BEFORE stopping when blocked: `echo "BLOCKED: <reason>" >> "$ORC_SIGNAL_FILE"`
- AFTER completing a task: `echo "DONE: <summary>" >> "$ORC_SIGNAL_FILE"`
- On errors you cannot recover from: `echo "ERROR: <message>" >> "$ORC_SIGNAL_FILE"`
]])
    f:close()
  end
end

--- Spawn a hidden terminal in a worktree and register it as a space.
---@param name string
---@param worktree_path string
---@param branch string
---@return boolean success
local function spawn_space(name, worktree_path, branch)
  local config = get_config()
  local signal_path = worktree_path .. "/" .. config.signal_file

  -- Skip setup for @main (user's own repo)
  if name ~= "@main" then
    local root = repo_root()
    if root then
      ensure_signal_instructions(root .. "/" .. config.worktree_base)

      -- Symlink all .claude files from root so spaces inherit settings, MCPs, etc.
      local claude_dir = worktree_path .. "/.claude"
      vim.fn.mkdir(claude_dir, "p")
      local root_claude_dir = root .. "/.claude"
      local handle = vim.uv.fs_scandir(root_claude_dir)
      if handle then
        while true do
          local entry, typ = vim.uv.fs_scandir_next(handle)
          if not entry then break end
          if typ == "file" and entry ~= "settings.local.json" then
            local dst = claude_dir .. "/" .. entry
            if vim.fn.filereadable(dst) == 0 and not vim.uv.fs_lstat(dst) then
              vim.uv.fs_symlink(root_claude_dir .. "/" .. entry, dst)
            end
          end
        end
      end

      -- Symlink root CLAUDE.md
      local root_claude = root .. "/CLAUDE.md"
      local dst_claude = worktree_path .. "/CLAUDE.md"
      if vim.fn.filereadable(root_claude) == 1 and vim.fn.filereadable(dst_claude) == 0 then
        vim.uv.fs_symlink(root_claude, dst_claude)
      end

      -- Build space settings: merge root permissions + signal permission + hooks
      local hooks_settings = claude_dir .. "/settings.local.json"
      local sig = signal_path:gsub('"', '\\"')

      -- Collect permissions: start with signal echo
      local allow = { string.format('Bash(echo * >> "%s")', sig) }

      -- Copy permissions from root settings.local.json
      local root_settings_path = root_claude_dir .. "/settings.local.json"
      local rf = io.open(root_settings_path, "r")
      if rf then
        local content = rf:read("*a")
        rf:close()
        local parse_ok, root_settings = pcall(vim.json.decode, content)
        if parse_ok and root_settings and root_settings.permissions and root_settings.permissions.allow then
          for _, perm in ipairs(root_settings.permissions.allow) do
            table.insert(allow, perm)
          end
        end
      end

      local settings = {
        permissions = { allow = allow },
        hooks = {
          Notification = {
            {
              matcher = "idle_prompt",
              hooks = {
                {
                  type = "command",
                  command = string.format('echo "QUESTION: Agent is waiting for input" >> "%s"', sig),
                },
              },
            },
            {
              matcher = "permission_prompt",
              hooks = {
                {
                  type = "command",
                  command = string.format('echo "BLOCKED: Agent needs tool permission" >> "%s"', sig),
                },
              },
            },
          },
          UserPromptSubmit = {
            {
              hooks = {
                {
                  type = "command",
                  command = string.format('echo "READY: Agent resumed" >> "%s"', sig),
                },
              },
            },
          },
        },
      }

      local hf = io.open(hooks_settings, "w")
      if hf then
        hf:write(vim.json.encode(settings))
        hf:close()
      end
    end
  end

  local bufnr = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_option_value("bufhidden", "hide", { buf = bufnr })

  local chan = vim.api.nvim_buf_call(bufnr, function()
    return vim.fn.termopen(config.cli, {
      cwd = worktree_path,
      env = { ORC_SIGNAL_FILE = signal_path },
      on_exit = function(_, code)
        vim.schedule(function()
          if M.spaces[name] then
            M.spaces[name].status = "exited"
            vim.notify("Orc: '" .. name .. "' CLI exited (code " .. code .. ")", vim.log.levels.INFO)
          end
        end)
      end,
    })
  end)

  if chan <= 0 then
    vim.api.nvim_buf_delete(bufnr, { force = true })
    return false
  end

  M.spaces[name] = {
    bufnr = bufnr,
    chan = chan,
    worktree_path = worktree_path,
    branch = branch,
    status = "active",
    win = nil,
    last_file = (name == "@main") and M._main_last_file or nil,
  }

  -- Cycle-space keymaps on the terminal buffer
  vim.api.nvim_buf_set_keymap(bufnr, "n", "<C-k>", "", {
    callback = function() M.cycle(-1) end,
    noremap = true,
    silent = true,
  })
  vim.api.nvim_buf_set_keymap(bufnr, "n", "<C-j>", "", {
    callback = function() M.cycle(1) end,
    noremap = true,
    silent = true,
  })

  if not M.active_space then
    M.active_space = name
  end

  if name ~= "@main" then
    local ok, signal = pcall(require, "orc.signal")
    if ok then
      signal.watch(name, worktree_path)
    end
  end

  return true
end

--- Check if a local branch exists.
---@param branch string
---@return boolean
local function branch_exists(branch)
  vim.fn.system({ "git", "rev-parse", "--verify", branch })
  return vim.v.shell_error == 0
end

--- List existing git worktrees as {path, branch} pairs.
---@return table<string, string> map of worktree_path → branch
local function existing_worktrees()
  local out = vim.fn.systemlist({ "git", "worktree", "list", "--porcelain" })
  local result = {}
  local current_path = nil
  for _, line in ipairs(out) do
    local path = line:match("^worktree (.+)$")
    if path then
      current_path = path
    end
    local branch_ref = line:match("^branch (.+)$")
    if branch_ref and current_path then
      result[current_path] = branch_ref:gsub("^refs/heads/", "")
    end
  end
  return result
end

--- State file path for the current repo.
---@return string|nil
local function state_path()
  local root = repo_root()
  if not root then
    return nil
  end
  local dir = vim.fn.stdpath("data") .. "/orc"
  vim.fn.mkdir(dir, "p")
  local hash = vim.fn.sha256(root):sub(1, 12)
  return dir .. "/" .. hash .. ".json"
end

--- Save space metadata to disk. Excludes @main (lazily created).
function M.save()
  local path = state_path()
  if not path then
    return
  end

  local data = {
    _active = M.active_space,
    _main_last_file = M._main_last_file,
    _main_last_cursor = M._main_last_cursor,
  }
  for name, space in pairs(M.spaces) do
    if name == "@main" then
      if space.last_file then
        data._main_last_file = space.last_file
        data._main_last_cursor = space.last_cursor
      end
    else
      data[name] = {
        worktree_path = space.worktree_path,
        branch = space.branch,
        last_file = space.last_file,
        last_cursor = space.last_cursor,
      }
    end
  end

  local json = vim.json.encode(data)
  local f = io.open(path, "w")
  if f then
    f:write(json)
    f:close()
  end
end

--- Restore spaces from disk, re-creating terminal buffers.
function M.restore()
  local path = state_path()
  if not path then
    return
  end

  local f = io.open(path, "r")
  if not f then
    return
  end

  local content = f:read("*a")
  f:close()

  if content == "" then
    return
  end

  local ok, data = pcall(vim.json.decode, content)
  if not ok or type(data) ~= "table" then
    return
  end

  local saved_active = data._active
  local main_last_file = data._main_last_file
  local main_last_cursor = data._main_last_cursor
  data._active = nil
  data._main_last_file = nil
  data._main_last_cursor = nil

  for name, info in pairs(data) do
    if type(info) == "table" and info.worktree_path and not M.spaces[name] and vim.fn.isdirectory(info.worktree_path) == 1 then
      spawn_space(name, info.worktree_path, info.branch)
      if M.spaces[name] then
        if info.last_file then
          M.spaces[name].last_file = info.last_file
        end
        if info.last_cursor then
          M.spaces[name].last_cursor = info.last_cursor
        end
      end
    end
  end

  if main_last_file then
    M._main_last_file = main_last_file
    M._main_last_cursor = main_last_cursor
    if M.spaces["@main"] then
      M.spaces["@main"].last_file = main_last_file
      M.spaces["@main"].last_cursor = main_last_cursor
    end
  end

  if saved_active then
    M.active_space = saved_active
  end
end

--- Get info about the main worktree.
---@return {path: string, branch: string}|nil
function M.main_worktree()
  local root = repo_root()
  if not root then
    return nil
  end
  local branch = vim.fn.systemlist("git -C " .. vim.fn.shellescape(root) .. " rev-parse --abbrev-ref HEAD")
  return {
    path = root,
    branch = (vim.v.shell_error == 0 and branch[1]) or "HEAD",
  }
end

--- Create a new space.
--- Supports: new branch, existing branch, or existing worktree.
---@param name string
---@param opts? {base?: string, branch?: string, worktree?: string}
function M.create(name, opts)
  opts = opts or {}

  if M.spaces[name] then
    vim.notify("Orc: space '" .. name .. "' already exists", vim.log.levels.WARN)
    return
  end

  local root = repo_root()
  if not root then
    vim.notify("Orc: not inside a git repository", vim.log.levels.ERROR)
    return
  end

  local config = get_config()
  local worktree_path, branch

  if opts.worktree then
    worktree_path = vim.fs.normalize(opts.worktree)
    if vim.fn.isdirectory(worktree_path) ~= 1 then
      vim.notify("Orc: worktree path does not exist: " .. worktree_path, vim.log.levels.ERROR)
      return
    end
    local wt_map = existing_worktrees()
    branch = wt_map[worktree_path] or name
  elseif opts.branch and branch_exists(opts.branch) then
    branch = opts.branch
    worktree_path = vim.fs.normalize(root .. "/" .. config.worktree_base .. "/" .. name)
    local result = vim.fn.system({ "git", "worktree", "add", "--force", worktree_path, branch })
    if vim.v.shell_error ~= 0 then
      vim.notify("Orc: failed to create worktree: " .. result, vim.log.levels.ERROR)
      return
    end
  else
    local base = opts.base or "HEAD"
    branch = opts.branch or name
    worktree_path = vim.fs.normalize(root .. "/" .. config.worktree_base .. "/" .. name)
    local result = vim.fn.system({ "git", "worktree", "add", "-b", branch, worktree_path, base })
    if vim.v.shell_error ~= 0 then
      vim.notify("Orc: failed to create worktree: " .. result, vim.log.levels.ERROR)
      return
    end
  end

  if not spawn_space(name, worktree_path, branch) then
    vim.notify("Orc: failed to start terminal", vim.log.levels.ERROR)
    return
  end

  vim.notify("Orc: created space '" .. name .. "'", vim.log.levels.INFO)
  M.switch(name)
end

--- Toggle visibility of a space's terminal.
---@param name? string Defaults to active space. Use "@main" for the main worktree.
function M.toggle(name)
  -- Prevent individual toggles from breaking grid layout
  local ok, grid = pcall(require, "orc.grid")
  if ok and grid.is_active() then
    vim.notify("Orc: close grid first (<leader>og) before toggling individual spaces", vim.log.levels.WARN)
    return
  end

  name = name or M.active_space or "@main"

  -- Lazily create a terminal for the main worktree
  if name == "@main" and not M.spaces["@main"] then
    local main = M.main_worktree()
    if not main then
      vim.notify("Orc: not inside a git repository", vim.log.levels.ERROR)
      return
    end
    if not spawn_space("@main", main.path, main.branch) then
      vim.notify("Orc: failed to start terminal", vim.log.levels.ERROR)
      return
    end
  end
  if not name then
    vim.notify("Orc: no active space", vim.log.levels.WARN)
    return
  end

  local space = M.spaces[name]
  if not space then
    vim.notify("Orc: space '" .. name .. "' not found", vim.log.levels.WARN)
    return
  end

  -- Respawn CLI if it exited
  if space.status == "exited" then
    if vim.api.nvim_buf_is_valid(space.bufnr) then
      vim.api.nvim_buf_delete(space.bufnr, { force = true })
    end
    if not spawn_space(name, space.worktree_path, space.branch) then
      vim.notify("Orc: failed to restart terminal", vim.log.levels.ERROR)
      return
    end
    space = M.spaces[name]
  end

  -- If visible, close it
  if space.win and vim.api.nvim_win_is_valid(space.win) then
    vim.api.nvim_win_close(space.win, true)
    space.win = nil
    return
  end

  -- Otherwise, open it
  local config = get_config()
  local win

  if config.terminal_direction == "float" then
    local width = math.floor(vim.o.columns * 0.8)
    local height = math.floor(vim.o.lines * 0.8)
    local row = math.floor((vim.o.lines - height) / 2)
    local col = math.floor((vim.o.columns - width) / 2)
    local display = (name == "@main") and "main" or name
    win = vim.api.nvim_open_win(space.bufnr, true, {
      relative = "editor",
      width = width,
      height = height,
      row = row,
      col = col,
      style = "minimal",
      border = "rounded",
      title = " Orc: " .. display .. " ",
      title_pos = "center",
    })
  elseif config.terminal_direction == "vertical" then
    vim.cmd("vsplit")
    win = vim.api.nvim_get_current_win()
    vim.api.nvim_win_set_buf(win, space.bufnr)
  else -- horizontal
    vim.cmd("split")
    win = vim.api.nvim_get_current_win()
    vim.api.nvim_win_set_buf(win, space.bufnr)
  end

  space.win = win
  if space.status == "needs_attention" then
    space.status = "active"
  end
end

--- Check if a worktree has uncommitted changes.
---@param worktree_path string
---@return boolean
function M.has_uncommitted_changes(worktree_path)
  local out = vim.fn.systemlist({ "git", "-C", worktree_path, "status", "--porcelain" })
  return vim.v.shell_error == 0 and #out > 0
end

--- Delete a space: kill terminal, remove worktree, clean up state.
--- The branch is kept so it can be reviewed from the main worktree.
---@param name string
---@param opts? {force?: boolean}
function M.delete(name, opts)
  opts = opts or {}
  if name == "@main" then
    vim.notify("Orc: cannot delete the main worktree", vim.log.levels.WARN)
    return
  end

  local space = M.spaces[name]
  if not space then
    vim.notify("Orc: space '" .. name .. "' not found", vim.log.levels.WARN)
    return
  end

  if not opts.force and M.has_uncommitted_changes(space.worktree_path) then
    local ui = require("orc.ui")
    ui.float_select("'" .. name .. "' has uncommitted changes. delete?", { "no", "yes" }, function(choice)
      if choice == "yes" then
        M.delete(name, { force = true })
      end
    end)
    return
  end

  -- Stop signal watcher
  local ok, signal = pcall(require, "orc.signal")
  if ok then
    signal.unwatch(name)
  end

  -- Close window if open
  if space.win and vim.api.nvim_win_is_valid(space.win) then
    vim.api.nvim_win_close(space.win, true)
  end

  -- Kill terminal
  if vim.api.nvim_buf_is_valid(space.bufnr) then
    vim.fn.jobstop(space.chan)
    vim.api.nvim_buf_delete(space.bufnr, { force = true })
  end

  -- Remove worktree (branch is preserved for review)
  local result = vim.fn.system({ "git", "worktree", "remove", "--force", space.worktree_path })
  if vim.v.shell_error ~= 0 then
    vim.notify("Orc: worktree removal warning: " .. result, vim.log.levels.WARN)
  end

  M.spaces[name] = nil

  if M.active_space == name then
    M.switch("@main")
  end

  M.save()
  vim.notify("Orc: deleted space '" .. name .. "' (branch " .. space.branch .. " kept)", vim.log.levels.INFO)
end

--- List all spaces (excludes @main).
---@return table<string, OrcSpace>
function M.list()
  local result = {}
  for name, space in pairs(M.spaces) do
    if name ~= "@main" then
      result[name] = space
    end
  end
  return result
end

--- Get the active space name.
---@return string|nil
function M.get_active()
  return M.active_space
end

--- Find the first non-floating, non-terminal window (the "editor" window).
---@return number|nil
local function find_editor_win()
  for _, win_id in ipairs(vim.api.nvim_list_wins()) do
    local config = vim.api.nvim_win_get_config(win_id)
    if config.relative == "" then
      local buf = vim.api.nvim_win_get_buf(win_id)
      if vim.bo[buf].buftype ~= "terminal" then
        return win_id
      end
    end
  end
  return nil
end

--- Switch the active space and open the current file in that worktree.
---@param name string
function M.switch(name)
  if name ~= "@main" and not M.spaces[name] then
    vim.notify("Orc: space '" .. name .. "' not found", vim.log.levels.WARN)
    return
  end

  -- Find the editor window so we read/write the correct buffer
  local editor_win = find_editor_win()

  -- Save current file and cursor for the space we're leaving
  local current = ""
  if editor_win then
    local buf = vim.api.nvim_win_get_buf(editor_win)
    current = vim.api.nvim_buf_get_name(buf)
  end

  if M.active_space and current ~= "" then
    local root = repo_root()
    if root then
      local old_space = M.spaces[M.active_space]
      local old_root = old_space and old_space.worktree_path or root
      if current:sub(1, #old_root + 1) == old_root .. "/" then
        local rel = current:sub(#old_root + 2)
        local cursor = editor_win and vim.api.nvim_win_get_cursor(editor_win) or nil
        if old_space then
          old_space.last_file = rel
          old_space.last_cursor = cursor
        end
        if M.active_space == "@main" then
          M._main_last_file = rel
          M._main_last_cursor = cursor
        end
      end
    end
  end

  -- Resolve target worktree path
  local target_root
  if name == "@main" then
    local main = M.main_worktree()
    target_root = main and main.path
  else
    target_root = M.spaces[name].worktree_path
  end

  -- Open file in target worktree: prefer target's last_file, fall back to current file's relative path
  if target_root then
    local target_space = M.spaces[name]
    local rel = target_space and target_space.last_file
    local target_cursor = target_space and target_space.last_cursor
    if not rel and name == "@main" then
      rel = M._main_last_file
      target_cursor = M._main_last_cursor
    end

    -- Fall back to current file's relative path in target worktree
    if not rel and current ~= "" then
      local root = repo_root()
      if root then
        for _, space in pairs(M.spaces) do
          local wp = space.worktree_path
          if current:sub(1, #wp + 1) == wp .. "/" then
            rel = current:sub(#wp + 2)
            break
          end
        end
        if not rel and current:sub(1, #root + 1) == root .. "/" then
          rel = current:sub(#root + 2)
        end
      end
      target_cursor = nil  -- no saved cursor for fallback
    end

    -- Ensure edit runs in the editor window, not a terminal window
    if editor_win then
      vim.api.nvim_set_current_win(editor_win)
    end

    if rel then
      local target_file = target_root .. "/" .. rel
      if vim.fn.filereadable(target_file) == 1 then
        vim.cmd("edit " .. vim.fn.fnameescape(target_file))
        if target_cursor then
          pcall(vim.api.nvim_win_set_cursor, 0, target_cursor)
        end
      else
        vim.cmd("enew")
      end
    else
      vim.cmd("enew")
    end
  end

  -- Handle window visibility across the switch
  local ok, grid = pcall(require, "orc.grid")
  if ok and grid.is_active() then
    -- In grid mode: swap the focused pane to the new space
    M.active_space = name
    M.save()
    grid.swap(name)
    vim.cmd("stopinsert")
    local display = (name == "@main") and "main" or name
    vim.notify("Orc: active space -> '" .. display .. "'", vim.log.levels.INFO)
    return
  end

  -- Check if the old active space had a visible window
  local old_win_visible = false
  if M.active_space then
    local old_space = M.spaces[M.active_space]
    if old_space and old_space.win and vim.api.nvim_win_is_valid(old_space.win) then
      old_win_visible = true
      vim.api.nvim_win_close(old_space.win, true)
      old_space.win = nil
    end
  end

  M.active_space = name
  M.save()
  vim.cmd("stopinsert")
  local display = (name == "@main") and "main" or name
  vim.notify("Orc: active space -> '" .. display .. "'", vim.log.levels.INFO)

  -- If the old space's window was visible, open the new space's window
  if old_win_visible then
    M.toggle(name)
  end
end

--- Update a space's status.
---@param name string
---@param status string
function M.set_status(name, status)
  if M.spaces[name] then
    M.spaces[name].status = status
  end
end

--- Get a space by name, or the active space.
---@param name? string
---@return OrcSpace|nil, string|nil
function M.get(name)
  name = name or M.active_space
  if not name then
    return nil, nil
  end
  return M.spaces[name], name
end

--- Get a sorted list of all space names.
---@return string[]
local function sorted_space_names()
  local names = vim.tbl_keys(M.spaces)
  table.sort(names, function(a, b)
    if a == "@main" then return true end
    if b == "@main" then return false end
    return a < b
  end)
  return names
end

--- Cycle to the next or previous space, opening its float if one was visible.
---@param direction number 1 for forward, -1 for backward
function M.cycle(direction)
  local names = sorted_space_names()
  if #names <= 1 then return end

  local current = M.active_space or "@main"
  local idx = 1
  for i, name in ipairs(names) do
    if name == current then
      idx = i
      break
    end
  end

  idx = ((idx - 1 + direction) % #names) + 1
  M.switch(names[idx])
end

--- Get space names for completion.
---@return string[]
function M.names()
  return vim.tbl_keys(M.spaces)
end

return M
