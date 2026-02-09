if vim.g.loaded_orc then
  return
end
vim.g.loaded_orc = true

vim.api.nvim_create_user_command("OrcCreate", function(cmd)
  local name = cmd.fargs[1]
  if not name or name == "" then
    vim.ui.input({ prompt = "Space name: " }, function(input)
      if input and input ~= "" then
        require("orc").create(input)
      end
    end)
    return
  end
  -- Parse optional flags: --branch=<branch> --worktree=<path>
  local opts = {}
  for i = 2, #cmd.fargs do
    local branch = cmd.fargs[i]:match("^%-%-branch=(.+)$")
    if branch then opts.branch = branch end
    local wt = cmd.fargs[i]:match("^%-%-worktree=(.+)$")
    if wt then opts.worktree = wt end
  end
  require("orc").create(name, opts)
end, {
  nargs = "*",
  desc = "Create a new Orc space (worktree + agent). Options: --branch=<name> --worktree=<path>",
})

vim.api.nvim_create_user_command("OrcToggle", function(cmd)
  require("orc").toggle(cmd.fargs[1])
end, {
  nargs = "?",
  complete = function()
    return require("orc").names()
  end,
  desc = "Toggle an Orc space terminal",
})

vim.api.nvim_create_user_command("OrcDelete", function(cmd)
  local name = cmd.fargs[1]
  if not name or name == "" then
    vim.notify("Orc: usage: :OrcDelete <name>", vim.log.levels.WARN)
    return
  end
  require("orc").delete(name)
end, {
  nargs = 1,
  complete = function()
    return require("orc").names()
  end,
  desc = "Delete an Orc space",
})

vim.api.nvim_create_user_command("OrcList", function()
  require("orc").list()
end, {
  desc = "List all Orc spaces",
})

vim.api.nvim_create_user_command("OrcPrompt", function(cmd)
  require("orc").prompt(cmd.fargs[1])
end, {
  nargs = "?",
  range = true,
  complete = function()
    return require("orc").names()
  end,
  desc = "Send a prompt to an Orc space",
})

vim.api.nvim_create_user_command("OrcGrid", function()
  require("orc").grid()
end, {
  desc = "Toggle Orc grid view (show multiple spaces)",
})

vim.api.nvim_create_user_command("OrcSwitch", function(cmd)
  local name = cmd.fargs[1]
  if not name or name == "" then
    vim.notify("Orc: usage: :OrcSwitch <name>", vim.log.levels.WARN)
    return
  end
  require("orc").switch(name)
end, {
  nargs = 1,
  complete = function()
    return require("orc").names()
  end,
  desc = "Switch the active Orc space",
})
