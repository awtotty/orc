local M = {}

local spaces = require("orc.spaces")

---@class OrcGridState
---@field active boolean
---@field tabnr number|nil
---@field wins table<number, string> win_id → space_name
---@field prev_tab number|nil

---@type OrcGridState
local state = {
  active = false,
  tabnr = nil,
  wins = {},
  prev_tab = nil,
}

--- Check if grid is currently active.
---@return boolean
function M.is_active()
  return state.active
end

--- Collect up to 4 spaces for the grid.
--- Active space first, then others alphabetically. Includes @main (lazy-created).
---@return string[]
local function collect_spaces()
  local names = {}
  local seen = {}

  -- Active space first
  local active = spaces.get_active()
  if active and spaces.spaces[active] then
    table.insert(names, active)
    seen[active] = true
  end

  -- Collect remaining space names, sorted
  local others = {}
  for name in pairs(spaces.spaces) do
    if not seen[name] then
      table.insert(others, name)
    end
  end
  table.sort(others)

  for _, name in ipairs(others) do
    if #names >= 4 then break end
    table.insert(names, name)
    seen[name] = true
  end

  -- Include @main if we have room and it's not already there
  if #names < 4 and not seen["@main"] then
    local main = spaces.main_worktree()
    if main then
      if not spaces.spaces["@main"] then
        -- Lazy-create @main terminal
        local bufnr = vim.api.nvim_create_buf(false, true)
        vim.api.nvim_set_option_value("bufhidden", "hide", { buf = bufnr })
        local chan = vim.api.nvim_buf_call(bufnr, function()
          local config = require("orc").config
          return vim.fn.termopen(config.cli, {
            cwd = main.path,
            env = { ORC_SIGNAL_FILE = main.path .. "/" .. config.signal_file },
            on_exit = function(_, code)
              vim.schedule(function()
                if spaces.spaces["@main"] then
                  spaces.spaces["@main"].status = "exited"
                end
              end)
            end,
          })
        end)
        if chan > 0 then
          spaces.spaces["@main"] = {
            bufnr = bufnr,
            chan = chan,
            worktree_path = main.path,
            branch = main.branch,
            status = "active",
            win = nil,
          }
          table.insert(names, "@main")
        end
      else
        table.insert(names, "@main")
      end
    end
  end

  return names
end

--- Respawn a space's terminal if it has exited.
---@param name string
local function ensure_alive(name)
  local space = spaces.spaces[name]
  if not space then return end
  if space.status ~= "exited" then return end

  if vim.api.nvim_buf_is_valid(space.bufnr) then
    vim.api.nvim_buf_delete(space.bufnr, { force = true })
  end

  local config = require("orc").config
  local bufnr = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_option_value("bufhidden", "hide", { buf = bufnr })

  local chan = vim.api.nvim_buf_call(bufnr, function()
    return vim.fn.termopen(config.cli, {
      cwd = space.worktree_path,
      env = { ORC_SIGNAL_FILE = space.worktree_path .. "/" .. config.signal_file },
      on_exit = function(_, code)
        vim.schedule(function()
          if spaces.spaces[name] then
            spaces.spaces[name].status = "exited"
          end
        end)
      end,
    })
  end)

  if chan > 0 then
    space.bufnr = bufnr
    space.chan = chan
    space.status = "active"
  end
end

--- Configure window options for a grid pane.
---@param win number
---@param name string
local function configure_win(win, name)
  vim.api.nvim_set_option_value("number", false, { win = win })
  vim.api.nvim_set_option_value("relativenumber", false, { win = win })
  vim.api.nvim_set_option_value("signcolumn", "no", { win = win })
  local display = (name == "@main") and "main" or name
  vim.api.nvim_set_option_value("winbar", " " .. display, { win = win })
end

--- Set up keymaps for grid terminal buffers.
---@param bufnr number
local function setup_buf_keymaps(bufnr)
  local nav = { h = "h", j = "j", k = "k", l = "l" }
  for key, dir in pairs(nav) do
    -- Terminal mode: escape terminal first, then navigate
    vim.keymap.set("t", "<C-" .. key .. ">", "<C-\\><C-n><C-w>" .. dir, {
      buffer = bufnr,
      desc = "Grid: navigate " .. dir,
    })
    -- Normal mode: standard window navigation
    vim.keymap.set("n", "<C-" .. key .. ">", "<C-w>" .. dir, {
      buffer = bufnr,
      desc = "Grid: navigate " .. dir,
    })
  end
end

--- Create the grid layout in the current tab.
---@param names string[]
---@return table<number, string> wins map
local function create_layout(names)
  local wins = {}
  local count = #names

  if count == 0 then return wins end

  -- First pane: current window
  local first_win = vim.api.nvim_get_current_win()
  local space = spaces.spaces[names[1]]
  vim.api.nvim_win_set_buf(first_win, space.bufnr)
  configure_win(first_win, names[1])
  setup_buf_keymaps(space.bufnr)
  space.win = first_win
  wins[first_win] = names[1]

  if count == 1 then
    return wins
  end

  if count == 2 then
    -- Vertical split: side by side
    vim.cmd("vsplit")
    local win2 = vim.api.nvim_get_current_win()
    local s2 = spaces.spaces[names[2]]
    vim.api.nvim_win_set_buf(win2, s2.bufnr)
    configure_win(win2, names[2])
    setup_buf_keymaps(s2.bufnr)
    s2.win = win2
    wins[win2] = names[2]
    return wins
  end

  if count == 3 then
    -- Top full-width + bottom two side-by-side
    vim.cmd("split")
    local win_bottom_left = vim.api.nvim_get_current_win()
    local s2 = spaces.spaces[names[2]]
    vim.api.nvim_win_set_buf(win_bottom_left, s2.bufnr)
    configure_win(win_bottom_left, names[2])
    setup_buf_keymaps(s2.bufnr)
    s2.win = win_bottom_left
    wins[win_bottom_left] = names[2]

    vim.cmd("vsplit")
    local win_bottom_right = vim.api.nvim_get_current_win()
    local s3 = spaces.spaces[names[3]]
    vim.api.nvim_win_set_buf(win_bottom_right, s3.bufnr)
    configure_win(win_bottom_right, names[3])
    setup_buf_keymaps(s3.bufnr)
    s3.win = win_bottom_right
    wins[win_bottom_right] = names[3]
    return wins
  end

  -- 4 spaces: 2x2 grid
  -- Start with top-left (already set), create top-right
  vim.cmd("vsplit")
  local win_top_right = vim.api.nvim_get_current_win()
  local s2 = spaces.spaces[names[2]]
  vim.api.nvim_win_set_buf(win_top_right, s2.bufnr)
  configure_win(win_top_right, names[2])
  setup_buf_keymaps(s2.bufnr)
  s2.win = win_top_right
  wins[win_top_right] = names[2]

  -- Go to top-left, split down for bottom-left
  vim.api.nvim_set_current_win(first_win)
  vim.cmd("split")
  local win_bottom_left = vim.api.nvim_get_current_win()
  local s3 = spaces.spaces[names[3]]
  vim.api.nvim_win_set_buf(win_bottom_left, s3.bufnr)
  configure_win(win_bottom_left, names[3])
  setup_buf_keymaps(s3.bufnr)
  s3.win = win_bottom_left
  wins[win_bottom_left] = names[3]

  -- Go to top-right, split down for bottom-right
  vim.api.nvim_set_current_win(win_top_right)
  vim.cmd("split")
  local win_bottom_right = vim.api.nvim_get_current_win()
  local s4 = spaces.spaces[names[4]]
  vim.api.nvim_win_set_buf(win_bottom_right, s4.bufnr)
  configure_win(win_bottom_right, names[4])
  setup_buf_keymaps(s4.bufnr)
  s4.win = win_bottom_right
  wins[win_bottom_right] = names[4]

  return wins
end

--- Set up autocmds for grid cleanup.
local function setup_autocmds()
  local group = vim.api.nvim_create_augroup("OrcGrid", { clear = true })

  vim.api.nvim_create_autocmd("TabClosed", {
    group = group,
    callback = function()
      if not state.active then return end
      -- Check if our tab still exists
      local tabs = vim.api.nvim_list_tabpages()
      local found = false
      for _, tab in ipairs(tabs) do
        if tab == state.tabnr then
          found = true
          break
        end
      end
      if not found then
        -- Grid tab was closed externally
        for win_id, name in pairs(state.wins) do
          local space = spaces.spaces[name]
          if space then
            space.win = nil
          end
        end
        state.active = false
        state.tabnr = nil
        state.wins = {}
        state.prev_tab = nil
        pcall(vim.api.nvim_del_augroup_by_name, "OrcGrid")
      end
    end,
  })
end

--- Open the grid view.
function M.open()
  if state.active then
    -- Already active, just focus the grid tab
    if state.tabnr and vim.api.nvim_tabpage_is_valid(state.tabnr) then
      vim.api.nvim_set_current_tabpage(state.tabnr)
    end
    return
  end

  local names = collect_spaces()
  if #names == 0 then
    vim.notify("Orc: no spaces available for grid", vim.log.levels.WARN)
    return
  end

  -- Respawn any exited spaces
  for _, name in ipairs(names) do
    ensure_alive(name)
  end

  -- Close any open toggle floats to prevent buffer-in-two-windows issues
  for _, name in ipairs(names) do
    local space = spaces.spaces[name]
    if space and space.win and vim.api.nvim_win_is_valid(space.win) then
      vim.api.nvim_win_close(space.win, true)
      space.win = nil
    end
  end

  -- Save current tab and create a new one
  state.prev_tab = vim.api.nvim_get_current_tabpage()
  vim.cmd("tabnew")
  local empty_buf = vim.api.nvim_get_current_buf()
  state.tabnr = vim.api.nvim_get_current_tabpage()

  -- Create the layout
  state.wins = create_layout(names)
  state.active = true

  -- Delete the orphaned [No Name] buffer created by tabnew
  if vim.api.nvim_buf_is_valid(empty_buf) and #vim.fn.win_findbuf(empty_buf) == 0 then
    vim.api.nvim_buf_delete(empty_buf, { force = true })
  end

  -- Set up autocmds
  setup_autocmds()

  -- Focus first pane (top-left)
  local first_win = nil
  for win_id, name in pairs(state.wins) do
    if name == names[1] then
      first_win = win_id
      break
    end
  end
  if first_win and vim.api.nvim_win_is_valid(first_win) then
    vim.api.nvim_set_current_win(first_win)
  end
  vim.cmd("stopinsert")
end

--- Close the grid view.
function M.close()
  if not state.active then return end

  -- Get focused pane's space name → set as active space
  local cur_win = vim.api.nvim_get_current_win()
  local focused_space = state.wins[cur_win]
  if focused_space then
    spaces.active_space = focused_space
    spaces.save()
  end

  -- Clear space.win for all grid spaces
  for _, name in pairs(state.wins) do
    local space = spaces.spaces[name]
    if space then
      space.win = nil
    end
  end

  -- Close the grid tab
  if state.tabnr and vim.api.nvim_tabpage_is_valid(state.tabnr) then
    local cur_tab = vim.api.nvim_get_current_tabpage()
    if cur_tab == state.tabnr then
      vim.cmd("tabclose")
    else
      -- We're somehow on a different tab; close the grid tab by number
      local tabnr_vim = vim.api.nvim_tabpage_get_number(state.tabnr)
      vim.cmd(tabnr_vim .. "tabclose")
    end
  end

  -- Clean up
  state.active = false
  state.tabnr = nil
  state.wins = {}
  state.prev_tab = nil
  pcall(vim.api.nvim_del_augroup_by_name, "OrcGrid")
end

--- Swap the focused grid pane to show a different space.
---@param name string The space name to swap in.
---@return boolean success
function M.swap(name)
  if not state.active then return false end

  local space = spaces.spaces[name]
  if not space then return false end

  -- Respawn if exited
  ensure_alive(name)
  space = spaces.spaces[name]
  if not space then return false end

  -- Find the currently focused window in the grid
  local cur_win = vim.api.nvim_get_current_win()
  local old_name = state.wins[cur_win]
  if not old_name then
    -- Focused window isn't a grid pane; use the first grid window
    for win_id, _ in pairs(state.wins) do
      if vim.api.nvim_win_is_valid(win_id) then
        cur_win = win_id
        old_name = state.wins[win_id]
        break
      end
    end
    if not old_name then return false end
  end

  -- Clear old space's win reference
  local old_space = spaces.spaces[old_name]
  if old_space then
    old_space.win = nil
  end

  -- Swap the buffer in the grid pane
  vim.api.nvim_win_set_buf(cur_win, space.bufnr)
  configure_win(cur_win, name)
  setup_buf_keymaps(space.bufnr)
  space.win = cur_win

  -- Update grid state
  state.wins[cur_win] = name

  return true
end

--- Toggle grid view.
function M.toggle()
  if state.active then
    M.close()
  else
    M.open()
  end
end

return M
