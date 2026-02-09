local M = {}

--- Open a small floating text input.
---@param title string
---@param callback fun(value: string)
---@param default? string Value used when input is empty
local function float_input(title, callback, default)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_option_value("bufhidden", "wipe", { buf = buf })

  local width = math.max(math.floor(vim.o.columns * 0.4), 30)
  local win = vim.api.nvim_open_win(buf, true, {
    relative = "editor",
    width = width,
    height = 1,
    row = math.floor((vim.o.lines - 1) / 2),
    col = math.floor((vim.o.columns - width) / 2),
    style = "minimal",
    border = "rounded",
    title = " " .. title .. " ",
    title_pos = "center",
  })

  vim.cmd("startinsert")

  local closed = false
  local function submit()
    if closed then return end
    closed = true
    local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
    vim.api.nvim_win_close(win, true)
    vim.cmd("stopinsert")
    local value = vim.trim(table.concat(lines, ""))
    value = value:gsub("%s+", "-")
    if value == "" then
      value = default
    end
    if value and value ~= "" then
      callback(value)
    end
  end
  local function cancel()
    if closed then return end
    closed = true
    vim.api.nvim_win_close(win, true)
    vim.cmd("stopinsert")
  end

  vim.keymap.set("i", "<CR>", submit, { buffer = buf, nowait = true })
  vim.keymap.set("n", "<CR>", submit, { buffer = buf, nowait = true })
  vim.keymap.set("n", "q", cancel, { buffer = buf, nowait = true })
  vim.keymap.set("n", "<Esc>", cancel, { buffer = buf, nowait = true })
end

--- Open a floating picker list.
---@param title string
---@param items string[]
---@param callback fun(item: string)
function M.float_select(title, items, callback)
  if #items == 0 then
    vim.notify("Orc: no items to select", vim.log.levels.WARN)
    return
  end

  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, items)
  vim.api.nvim_set_option_value("modifiable", false, { buf = buf })
  vim.api.nvim_set_option_value("bufhidden", "wipe", { buf = buf })

  local width = 0
  for _, item in ipairs(items) do
    width = math.max(width, vim.fn.strdisplaywidth(item))
  end
  width = math.max(width + 4, 30)
  local height = math.min(#items, math.floor(vim.o.lines * 0.5))

  local win = vim.api.nvim_open_win(buf, true, {
    relative = "editor",
    width = width,
    height = height,
    row = math.floor((vim.o.lines - height) / 2),
    col = math.floor((vim.o.columns - width) / 2),
    style = "minimal",
    border = "rounded",
    title = " " .. title .. " ",
    title_pos = "center",
  })
  vim.api.nvim_set_option_value("cursorline", true, { win = win })

  local function close()
    if vim.api.nvim_win_is_valid(win) then
      vim.api.nvim_win_close(win, true)
    end
  end

  vim.keymap.set("n", "<CR>", function()
    local row = vim.api.nvim_win_get_cursor(win)[1]
    local item = items[row]
    close()
    if item then
      callback(item)
    end
  end, { buffer = buf, nowait = true })

  vim.keymap.set("n", "q", close, { buffer = buf, nowait = true })
  vim.keymap.set("n", "<Esc>", close, { buffer = buf, nowait = true })
  vim.api.nvim_create_autocmd("BufLeave", { buffer = buf, once = true, callback = close })
end

--- Get local git branches (excluding current).
---@return string[]
local function git_branches()
  local out = vim.fn.systemlist({ "git", "branch", "--format=%(refname:short)" })
  if vim.v.shell_error ~= 0 then
    return {}
  end
  return out
end

--- Get existing git worktrees as display strings.
---@return string[] display, table<number, {path: string, branch: string}> map
local function git_worktrees()
  local out = vim.fn.systemlist({ "git", "worktree", "list", "--porcelain" })
  local results = {}
  local current_path = nil
  for _, line in ipairs(out) do
    local path = line:match("^worktree (.+)$")
    if path then
      current_path = path
    end
    local branch_ref = line:match("^branch (.+)$")
    if branch_ref and current_path then
      table.insert(results, { path = current_path, branch = branch_ref:gsub("^refs/heads/", "") })
      current_path = nil
    end
  end

  -- Filter out worktrees already tracked as spaces
  local orc = require("orc")
  local existing = orc.spaces()
  local tracked_paths = {}
  for _, space in pairs(existing) do
    tracked_paths[space.worktree_path] = true
  end
  local main = orc.main_worktree()
  if main then
    tracked_paths[main.path] = true
  end

  local display = {}
  local map = {}
  for _, wt in ipairs(results) do
    if not tracked_paths[wt.path] then
      table.insert(display, wt.branch .. "  " .. wt.path)
      map[#display] = wt
    end
  end
  return display, map
end

--- Open the spaces list in a floating buffer.
function M.list()
  local orc = require("orc")
  local entries = orc.spaces()
  local active = orc.get_active()

  local lines = {}
  local line_to_name = {}

  -- Main worktree always listed first
  local main = orc.main_worktree()
  if main then
    local marker = (active == "@main" or active == nil) and " ● " or "   "
    table.insert(lines, marker .. "main  " .. main.branch .. "  " .. main.path)
    line_to_name[#lines] = "@main"
  end

  local sorted = {}
  for name in pairs(entries) do
    table.insert(sorted, name)
  end
  table.sort(sorted)

  for _, name in ipairs(sorted) do
    local space = entries[name]
    local marker = (name == active) and " ● " or "   "
    table.insert(lines, marker .. name .. "  [" .. space.status .. "]  " .. space.branch)
    line_to_name[#lines] = name
  end

  -- Action lines
  local actions = {}
  table.insert(lines, "   + new space      (n)")
  actions[#lines] = "new"
  table.insert(lines, "   + from branch    (b)")
  actions[#lines] = "branch"
  table.insert(lines, "   + from worktree  (w)")
  actions[#lines] = "worktree"

  -- Create buffer
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.api.nvim_set_option_value("modifiable", false, { buf = buf })
  vim.api.nvim_set_option_value("bufhidden", "wipe", { buf = buf })

  local width = 0
  for _, line in ipairs(lines) do
    width = math.max(width, vim.fn.strdisplaywidth(line))
  end
  width = math.max(width + 4, 30)
  local height = #lines

  -- Open float
  local win = vim.api.nvim_open_win(buf, true, {
    relative = "editor",
    width = width,
    height = height,
    row = math.floor((vim.o.lines - height) / 2),
    col = math.floor((vim.o.columns - width) / 2),
    style = "minimal",
    border = "rounded",
    title = " Orc Spaces ",
    title_pos = "center",
  })

  vim.api.nvim_set_option_value("cursorline", true, { win = win })

  -- Place cursor on the first space line (find minimum key)
  local first_line = nil
  for k in pairs(line_to_name) do
    if not first_line or k < first_line then
      first_line = k
    end
  end
  if first_line then
    vim.api.nvim_win_set_cursor(win, { first_line, 0 })
  end

  local close = function()
    if vim.api.nvim_win_is_valid(win) then
      vim.api.nvim_win_close(win, true)
    end
  end

  -- <CR>: switch to space or run create action
  vim.keymap.set("n", "<CR>", function()
    local row = vim.api.nvim_win_get_cursor(win)[1]
    local action = actions[row]
    close()

    if action == "new" then
      float_input("Space name", function(name)
        orc.create(name)
      end)
    elseif action == "branch" then
      local branches = git_branches()
      M.float_select("Select branch", branches, function(branch_name)
        local default_name = branch_name:gsub("/", "-")
        float_input("Space name [" .. default_name .. "]", function(name)
          orc.create(name, { branch = branch_name })
        end, default_name)
      end)
    elseif action == "worktree" then
      local display, wt_map = git_worktrees()
      if #display == 0 then
        vim.notify("Orc: no untracked worktrees found", vim.log.levels.WARN)
      else
        M.float_select("Select worktree", display, function(item)
          local idx
          for i, d in ipairs(display) do
            if d == item then
              idx = i
              break
            end
          end
          if not idx or not wt_map[idx] then return end
          local wt = wt_map[idx]
          local default_name = wt.branch:gsub("/", "-")
          float_input("Space name [" .. default_name .. "]", function(name)
            orc.create(name, { worktree = wt.path })
          end, default_name)
        end)
      end
    elseif line_to_name[row] then
      orc.switch(line_to_name[row])
    end
  end, { buffer = buf, nowait = true })

  -- d: delete space with confirmation (not on main)
  vim.keymap.set("n", "d", function()
    local row = vim.api.nvim_win_get_cursor(win)[1]
    local name = line_to_name[row]
    if not name or name == "@main" then
      return
    end
    close()
    M.float_select("delete '" .. name .. "'?", { "no", "yes" }, function(choice)
      if choice == "yes" then
        local spaces = require("orc.spaces")
        local space = spaces.spaces[name]
        if space and spaces.has_uncommitted_changes(space.worktree_path) then
          M.float_select("'" .. name .. "' has uncommitted changes. delete?", { "no", "yes" }, function(choice2)
            if choice2 == "yes" then
              orc.delete(name, { force = true })
            end
          end)
        else
          orc.delete(name, { force = true })
        end
      end
    end)
  end, { buffer = buf, nowait = true })

  -- n: new space shortcut
  vim.keymap.set("n", "n", function()
    close()
    float_input("Space name", function(name)
      orc.create(name)
    end)
  end, { buffer = buf, nowait = true })

  -- b: from branch shortcut
  vim.keymap.set("n", "b", function()
    close()
    local branches = git_branches()
    M.float_select("Select branch", branches, function(branch_name)
      local default_name = branch_name:gsub("/", "-")
      float_input("Space name [" .. default_name .. "]", function(name)
        orc.create(name, { branch = branch_name })
      end, default_name)
    end)
  end, { buffer = buf, nowait = true })

  -- w: from worktree shortcut
  vim.keymap.set("n", "w", function()
    close()
    local display, wt_map = git_worktrees()
    if #display == 0 then
      vim.notify("Orc: no untracked worktrees found", vim.log.levels.WARN)
    else
      M.float_select("Select worktree", display, function(item)
        local idx
        for i, d in ipairs(display) do
          if d == item then
            idx = i
            break
          end
        end
        if not idx or not wt_map[idx] then return end
        local wt = wt_map[idx]
        local default_name = wt.branch:gsub("/", "-")
        float_input("Space name [" .. default_name .. "]", function(name)
          orc.create(name, { worktree = wt.path })
        end, default_name)
      end)
    end
  end, { buffer = buf, nowait = true })

  vim.keymap.set("n", "q", close, { buffer = buf, nowait = true })
  vim.keymap.set("n", "<Esc>", close, { buffer = buf, nowait = true })
  vim.api.nvim_create_autocmd("BufLeave", { buffer = buf, once = true, callback = close })
end

return M
