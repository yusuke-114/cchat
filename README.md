# cchat

Lightweight terminal chat for Claude, powered by your own API key or gateway.

Put your API key (or a gateway URL and token) in a config file, run `cchat`, and chat in your terminal:

- Multi-turn sessions that you can resume later
- Markdown rendering, plus highlighted diffs for every file edit
- Shell commands and their output shown inline
- Switch model, effort and permission mode on the fly
- Local MCP servers, configured in the same file

Under the hood, cchat is a single Python script on top of Claude Code's headless mode (`claude -p`).
Claude can still read and edit files and run commands in your project, but you get a minimal chat UI
and your credentials stay out of `~/.claude/settings.json`.

Requirements: macOS or Linux, and Claude Code (`setup.sh` installs it for you).

## Quick start

**Step 1. Clone and install**

    git clone https://github.com/yusuke-114/cchat.git
    cd cchat
    ./setup.sh

If setup says `~/.local/bin` is not in your PATH, run the command it prints, then open a new terminal.

**Step 2. Create your config file** (skip this step if `claude` is already logged in and you want to use that login)

    mkdir -p ~/.config/cchat
    cp cchat.config.example.json ~/.config/cchat/cchat.config.json
    chmod 600 ~/.config/cchat/cchat.config.json

cchat uses this JSON file instead of a `.env` file. Open it and fill in your values:

    {
      "base_url": "https://your-gateway.example.com",
      "auth_token": "your-api-key",
      "model": "claude-opus-5-5"
    }

- Calling the Anthropic API directly: delete `base_url` and `auth_token`, and set `"api_key": "sk-ant-..."`.
- Going through a gateway or proxy: set `base_url` (without `/v1/messages`) and `auth_token`.
- Fields you don't need can be deleted. See [Configuration](#configuration) for every field.

**Step 3. Check that it works**

    cchat "hello"

You should get a reply followed by a cost line such as `── $0.0012 (total $0.0012) · 1.8s · 1 turns · session 1a2b3c4d`.
If you get an auth error, check `base_url` and the key in step 2.

**Step 4. Use it in your project**

    cd /path/to/your-project
    cchat

Type a question and press Enter. Type `/help` to list commands and `/exit` to quit.
When you exit, cchat prints a `cchat --resume <id>` command that you can use to continue the session later.

## Setup details
`./setup.sh` will:
1. Install uv if it's missing (prefers brew)
2. Install Claude Code if it's missing (prefers `brew install --cask claude-code`)
3. Create the `~/.local/bin/cchat` command
4. Download rich, which is used for Markdown and syntax highlighting

## Configuration
The config file is optional, and cchat never modifies `~/.claude/settings.json`.

    mkdir -p ~/.config/cchat
    cp cchat.config.example.json ~/.config/cchat/cchat.config.json
    chmod 600 ~/.config/cchat/cchat.config.json

cchat looks for a config file in this order: `-c <path>`, then `~/.config/cchat/cchat.config.json`, then `cchat.config.json` next to `cchat.py`.
It never reads a config from the current directory, so an untrusted repo can't redirect your
credentials or inject environment variables. Without a config file, claude uses its normal login.

| Field | Purpose |
|---|---|
| base_url | Gateway URL, without `/v1/messages` |
| auth_token | Sent as `Authorization: Bearer` |
| api_key | Sent as `x-api-key` (use either this or auth_token) |
| model / effort / permission_mode / allowed_tools | Defaults; command-line flags override them |
| setting_sources | Which settings layers claude reads. The default is `project,local` (skips `~/.claude/settings.json`). Use `user,project,local` to read all of them |
| mcp_servers | Local MCP servers, passed to claude with `--mcp-config` (doesn't depend on `~/.claude.json`); see the example below |
| env | Extra environment variables, passed to claude unchanged |

Credentials are passed to claude through environment variables and a temporary settings file
with mode 0600. They never appear on the command line, so they can't be read from `ps`.

mcp_servers example (stdio):

    "mcp_servers": {
      "my-tool": { "command": "/absolute/path/to/server", "args": ["--stdio"], "env": {} }
    }

## Usage
    cd your-project && cchat             # interactive mode
    cchat "explain main.py"              # one-shot question
    cchat -r <session-id>                # resume a session
    cchat -m opus -e high --mode plan    # set model / effort / permission mode for this run
    cchat --allow "Bash(npm test)" --allow "Bash(python3 *)"
    cchat -s ./extra-settings.json       # merge an extra settings file (or inline JSON)

In-session commands: `/new` `/id` `/resume` `/model` `/effort` `/mode` `/help` `/exit`.
End a line with `\` to keep typing on a new line. Press Ctrl+C to interrupt a response.

## Uninstall
    ./uninstall.sh

This removes the `cchat` command and your input history. It asks before clearing the rich cache
and before deleting each `cchat.config.json`. It doesn't touch uv, claude, or `~/.claude/`
(the CLI's settings.json and session history).

## License
[MIT](LICENSE)
