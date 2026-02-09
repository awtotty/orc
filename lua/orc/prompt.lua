local M = {}

--- Get the visual selection lines from the current buffer.
---@return string[]|nil lines, string|nil filename, number|nil start_line, number|nil end_line
local function get_visual_selection()
  local start_pos = vim.fn.getpos("'<")
  local end_pos = vim.fn.getpos("'>")
  local start_line = start_pos[2]
  local end_line = end_pos[2]

  if start_line == 0 or end_line == 0 or start_line > end_line then
    return nil, nil, nil, nil
  end

  local bufnr = vim.api.nvim_get_current_buf()
  local lines = vim.api.nvim_buf_get_lines(bufnr, start_line - 1, end_line, false)
  local filename = vim.fn.fnamemodify(vim.api.nvim_buf_get_name(bufnr), ":~:.")

  return lines, filename, start_line, end_line
end

--- Detect the filetype for fenced code block language.
---@return string
local function get_lang()
  local ft = vim.bo.filetype
  if ft == "" then
    return ""
  end
  return ft
end

--- Format the context block.
---@param lines string[]
---@param filename string
---@param start_line number
---@param end_line number
---@return string
local function format_context(lines, filename, start_line, end_line)
  local lang = get_lang()
  local header = string.format("In `%s:%d-%d`:", filename, start_line, end_line)
  local fence_open = "```" .. lang
  local fence_close = "```"
  local code = table.concat(lines, "\n")

  return header .. "\n" .. fence_open .. "\n" .. code .. "\n" .. fence_close
end

--- Send text to a space's terminal, then open and focus it.
---@param space_name? string
---@param text string
local function send_to_space(space_name, text)
  local orc = require("orc")
  local space, name = orc.get(space_name)

  if not space then
    vim.notify("Orc: no space to send to", vim.log.levels.WARN)
    return
  end

  -- Open and focus the terminal (toggle handles respawning exited spaces)
  if not (space.win and vim.api.nvim_win_is_valid(space.win)) then
    orc.toggle(name)
    space = orc.get(name)
    if not space then return end
  else
    vim.api.nvim_set_current_win(space.win)
    vim.cmd("startinsert")
  end

  -- Paste via Neovim's API (sends bracketed paste through the terminal emulator)
  -- then submit with a trailing newline
  vim.api.nvim_paste(text .. "\n", true, -1)
end

--- Open a floating prompt input, then send to a space.
--- Optionally includes visual selection as context.
---@param space_name? string
function M.prompt(space_name)
  -- Capture visual selection only when called from visual mode
  local context = nil
  local mode = vim.fn.mode()
  if mode == "v" or mode == "V" or mode == "\22" then
    local esc = vim.api.nvim_replace_termcodes("<Esc>", true, false, true)
    vim.api.nvim_feedkeys(esc, "nx", false)
    local sel_lines, filename, start_line, end_line = get_visual_selection()
    if sel_lines and #sel_lines > 0 then
      context = format_context(sel_lines, filename, start_line, end_line)
    end
  end

  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_option_value("bufhidden", "wipe", { buf = buf })
  vim.api.nvim_set_option_value("filetype", "markdown", { buf = buf })

  local width = math.floor(vim.o.columns * 0.6)
  local win = vim.api.nvim_open_win(buf, true, {
    relative = "editor",
    width = width,
    height = 1,
    row = math.floor((vim.o.lines - 1) / 2),
    col = math.floor((vim.o.columns - width) / 2),
    style = "minimal",
    border = "rounded",
    title = " Orc Prompt ",
    title_pos = "center",
  })

  vim.cmd("startinsert")

  local closed = false
  local function close_and_send()
    if closed then return end
    closed = true

    local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
    vim.api.nvim_win_close(win, true)

    local input = vim.trim(table.concat(lines, "\n"))
    if input == "" then
      return
    end

    local message
    if context then
      message = context .. "\n\n" .. input
    else
      message = input
    end

    send_to_space(space_name, message)
  end

  local function cancel()
    if closed then return end
    closed = true
    vim.api.nvim_win_close(win, true)
  end

  vim.keymap.set("i", "<CR>", close_and_send, { buffer = buf, nowait = true })
  vim.keymap.set("n", "<CR>", close_and_send, { buffer = buf, nowait = true })
  vim.keymap.set("n", "q", cancel, { buffer = buf, nowait = true })
  vim.keymap.set("n", "<Esc>", cancel, { buffer = buf, nowait = true })
end

return M
