Idea: Write a neovim plugin for how you want to work with AI agents.

Branding: Orc(hestrator)

Design notes:

- Just be able to use existing CLIs (Claude, Codex, etc.)
- Manage worktrees and git branches with working agents (Spaces/Rooms)
- Agents work on their own and bring that work to the main space/room for review
- Easily define tasks and see what tasks each agent is working on
- Code review
- Chat with an agent about a branch
- Chat with an agent about a block of code and kick off a change (e.g. refactor this to be...)
- Skills autodetection

Concept:

- Space buffer
  - Pull up a buffer to see agent spaces and progress
  - Select a space, create a new space, etc.
  - Spaces are worktrees/branches for agents
  - See "notifications" of work ready for review, questions asked, etc. by space
- Prompt buffer
  - Optionally select lines to reference
  - Write a prompt
  - Control modes, etc. (this is just a wrapper on the CLI of your choosing)
  - Queue prompts in a space (maybe just supported by the CLI)
- Review mode
  - Open changes in a branch in the main buffer (select diff ref)
  - Tab through changes
  - Open prompt buffer at any point to prompt changes
