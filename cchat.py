#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["rich"]
# ///
"""cchat - lightweight terminal chat for Claude, powered by your own API key or gateway

Runs `claude -p --output-format stream-json` for multi-turn chat and renders
Markdown, code diffs, file writes and command execution.
"""
import argparse
import atexit
import difflib
import json
import os
import readline  # noqa: F401  enables arrow keys / history in input()
import shutil
import subprocess
import sys
import tempfile

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CONFIG_NAME = "cchat.config.json"
USER_CONFIG = os.path.expanduser(f"~/.config/cchat/{CONFIG_NAME}")
HISTORY_FILE = os.path.expanduser("~/.cchat_history")
HELP = """\
[bold]Commands[/]
  /new            Start a new session
  /id             Show the current session id
  /resume <id>    Switch to a session
  /model <name>   Switch model (opus / sonnet / haiku or a full ID)
  /effort <level> Set effort: low / medium / high / xhigh / max
  /mode <mode>    Set permission mode: default / acceptEdits / plan / bypassPermissions
  /help           Show this help
  /exit           Quit (or Ctrl+D)
[bold]Input[/]
  End a line with \\ to continue on the next line; Ctrl+C interrupts a response
"""


def find_config():
    """Config lookup: ~/.config/cchat → directory of cchat.py.

    The current directory is intentionally NOT searched: a config planted in an
    untrusted repo could redirect base_url or inject env vars (e.g. NODE_OPTIONS).
    """
    for path in (USER_CONFIG, os.path.join(SCRIPT_DIR, CONFIG_NAME)):
        if os.path.isfile(path):
            return path
    return None


def load_config(path):
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def config_env(cfg):
    """Turn the config into env vars passed only to the claude subprocess (nothing is written to disk)"""
    env = {}
    if cfg.get("base_url"):
        env["ANTHROPIC_BASE_URL"] = cfg["base_url"]
    if cfg.get("api_key"):
        env["ANTHROPIC_API_KEY"] = cfg["api_key"]        # sent as the x-api-key header
    if cfg.get("auth_token"):
        env["ANTHROPIC_AUTH_TOKEN"] = cfg["auth_token"]  # sent as Authorization: Bearer
    env.update({k: str(v) for k, v in (cfg.get("env") or {}).items()})
    return env


def build_settings_file(cfg_env, extra):
    """Merge config env with the -s/--settings value into one 0600 temp file.

    Passing secrets as inline JSON on the command line would expose them via `ps`,
    and passing --settings twice would let the later one silently drop the env.
    Returns the temp file path (removed at exit), the original value, or None.
    """
    if not cfg_env:
        return extra
    settings = {}
    if extra:
        if os.path.isfile(extra):
            with open(extra, encoding="utf-8") as f:
                settings = json.load(f)
        else:
            settings = json.loads(extra)
    # Values in -s/--settings take precedence over the config file
    settings["env"] = {**cfg_env, **(settings.get("env") or {})}
    fd, path = tempfile.mkstemp(prefix="cchat-", suffix=".json")  # created with mode 0600
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(settings, f)
    atexit.register(lambda: os.path.exists(path) and os.remove(path))
    return path


def rel(path):
    """Show an absolute path relative to cwd (unchanged if it is outside cwd)"""
    try:
        r = os.path.relpath(path)
        return path if r.startswith("..") else r
    except ValueError:
        return path


class Chat:
    def __init__(self, args, cfg_env, settings_path):
        self.cfg_env = cfg_env
        self.settings_path = settings_path
        self.session_id = args.resume
        self.model = args.model
        self.effort = args.effort
        self.mode = args.mode
        self.allowed = args.allow
        self.setting_sources = args.setting_sources
        self.mcp_servers = args.mcp_servers
        self.total_cost = 0.0

    # ---------- rendering ----------
    def show_diff(self, path, old, new):
        path = rel(path)
        diff = "\n".join(difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            f"a/{path}", f"b/{path}", lineterm=""))
        console.print(Panel(Syntax(diff or "(no changes)", "diff", theme="monokai"),
                            title=f"✏️  {path}", title_align="left", border_style="cyan"))

    def show_tool(self, name, inp):
        if name == "Edit":
            self.show_diff(inp.get("file_path", "?"), inp.get("old_string", ""), inp.get("new_string", ""))
        elif name == "MultiEdit":
            for e in inp.get("edits", []):
                self.show_diff(inp.get("file_path", "?"), e.get("old_string", ""), e.get("new_string", ""))
        elif name == "Write":
            path, code = rel(inp.get("file_path", "?")), inp.get("content", "")
            console.print(Panel(
                Syntax(code, Syntax.guess_lexer(path, code), theme="monokai", line_numbers=True),
                title=f"📝 {path}", title_align="left", border_style="green"))
        elif name == "Bash":
            console.print(f"[bold yellow]$ {inp.get('command', '')}[/]")
        elif name in ("Read", "Glob", "Grep"):
            target = inp.get("file_path") or inp.get("pattern") or ""
            console.print(f"[dim]🔍 {name} {rel(target) if name == 'Read' else target}[/]")
        else:
            console.print(f"[dim]🔧 {name} {json.dumps(inp, ensure_ascii=False)[:150]}[/]")

    def show_tool_result(self, block):
        content = block.get("content", "")
        if isinstance(content, list):
            content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        content = str(content).strip()
        if block.get("is_error"):
            console.print(f"[red]  ✗ {content[:300]}[/]")
        elif content and self._tool_names.get(block.get("tool_use_id")) == "Bash":
            lines = content.splitlines()
            shown = "\n".join(lines[:15]) + (f"\n… ({len(lines)} lines total)" if len(lines) > 15 else "")
            console.print(Panel(shown, border_style="dim", style="dim"))

    # ---------- calling claude -p ----------
    def build_cmd(self):
        # The prompt is sent via stdin, so prompts starting with "-" are not parsed as flags
        cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose",
               "--permission-mode", self.mode]
        if self.allowed:
            cmd += ["--allowedTools", *self.allowed]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        if self.setting_sources:
            # Default project,local: ~/.claude/settings.json is not read
            cmd += ["--setting-sources", self.setting_sources]
        if self.mcp_servers:
            # Local MCP servers come only from the config file, not ~/.claude.json
            cmd += ["--mcp-config", json.dumps({"mcpServers": self.mcp_servers})]
        if self.settings_path:
            # --settings outranks ~/.claude/settings.json but only applies to this process
            cmd += ["--settings", self.settings_path]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        return cmd

    def ask(self, prompt):
        self._tool_names = {}  # tool_use_id -> tool name
        # stderr goes to a temp file so a full pipe can never block claude
        with tempfile.TemporaryFile(mode="w+") as errf:
            proc = subprocess.Popen(self.build_cmd(), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=errf, text=True,
                                    env={**os.environ, **self.cfg_env})
            got_result = False
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
                with console.status("[dim]Thinking…[/]", spinner="dots") as status:
                    for line in proc.stdout:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            console.print(f"[dim]{line}[/]")
                            continue
                        t = ev.get("type")
                        if t == "system" and ev.get("subtype") == "init":
                            self.session_id = ev.get("session_id", self.session_id)
                        elif t == "assistant":
                            status.stop()
                            for b in ev.get("message", {}).get("content", []):
                                if b.get("type") == "text" and b.get("text", "").strip():
                                    console.print(Markdown(b["text"], code_theme="monokai"))
                                elif b.get("type") == "tool_use":
                                    self._tool_names[b.get("id")] = b.get("name")
                                    self.show_tool(b.get("name"), b.get("input", {}))
                            status.start()
                        elif t == "user":
                            content = ev.get("message", {}).get("content", [])
                            if isinstance(content, list):
                                status.stop()
                                for b in content:
                                    if isinstance(b, dict) and b.get("type") == "tool_result":
                                        self.show_tool_result(b)
                                status.start()
                        elif t == "result":
                            got_result = True
                            self.session_id = ev.get("session_id", self.session_id)
                            cost = ev.get("total_cost_usd") or 0
                            self.total_cost += cost
                            if ev.get("is_error"):
                                console.print(f"[red]✗ {ev.get('result') or ev.get('subtype')}[/]")
                            console.print(
                                f"[dim]── ${cost:.4f} (total ${self.total_cost:.4f}) · "
                                f"{(ev.get('duration_ms') or 0) / 1000:.1f}s · "
                                f"{ev.get('num_turns', '?')} turns · session {str(self.session_id)[:8]}[/]")
            except KeyboardInterrupt:
                proc.terminate()
                console.print("\n[red]Interrupted[/]")
            except BrokenPipeError:
                pass  # claude exited before reading stdin; its stderr is shown below
            proc.wait()
            errf.seek(0)
            err = errf.read().strip()
        if proc.returncode not in (0, None, -15) and not got_result:
            console.print(f"[red]claude exited with code {proc.returncode}[/]")
        if err and not got_result:
            console.print(f"[red]{err[-1500:]}[/]")

    # ---------- main loop ----------
    def read_input(self):
        lines = [input("\n\001\033[1;35m\002❯\001\033[0m\002 ")]
        while lines[-1].endswith("\\"):
            lines[-1] = lines[-1][:-1]
            lines.append(input("\001\033[2m\002…\001\033[0m\002 "))
        return "\n".join(lines).strip()

    def handle_command(self, q):
        parts = q.split(maxsplit=1)
        cmd, arg = parts[0], (parts[1] if len(parts) > 1 else "")
        needs_arg = {"/resume": "<id>", "/model": "<name>", "/effort": "<level>", "/mode": "<mode>"}
        if cmd in needs_arg and not arg:
            console.print(f"[yellow]Usage: {cmd} {needs_arg[cmd]}[/]")
        elif cmd in ("/exit", "/quit"):
            raise EOFError
        elif cmd == "/new":
            self.session_id = None
            console.print("[dim]Started a new session[/]")
        elif cmd == "/id":
            console.print(f"session: {self.session_id or '(new session)'}")
        elif cmd == "/resume":
            self.session_id = arg
            console.print(f"[dim]Switched to {arg}[/]")
        elif cmd == "/model":
            self.model = arg
            console.print(f"[dim]model → {arg}[/]")
        elif cmd == "/effort":
            self.effort = arg
            console.print(f"[dim]effort → {arg}[/]")
        elif cmd == "/mode":
            self.mode = arg
            console.print(f"[dim]permission mode → {arg}[/]")
        elif cmd == "/help":
            console.print(HELP)
        else:
            # Any other /-prefixed input goes to claude as-is (e.g. custom commands)
            return False
        return True

    def run(self):
        try:
            readline.read_history_file(HISTORY_FILE)
        except (FileNotFoundError, OSError):
            pass
        console.print(Panel(
            f"cchat · dir [bold]{os.getcwd()}[/] · mode [bold]{self.mode}[/]"
            + (f" · model [bold]{self.model}[/]" if self.model else "")
            + (f" · effort [bold]{self.effort}[/]" if self.effort else "")
            + f"\nsetting sources [bold]{self.setting_sources}[/]"
            + (f"\nconfig {self.config_path}" if self.config_path else "\nconfig (none, using claude's default login)")
            + (f" · baseurl [bold]{self.cfg_env['ANTHROPIC_BASE_URL']}[/]"
               if "ANTHROPIC_BASE_URL" in self.cfg_env else "")
            + (f"\nMCP [bold]{', '.join(self.mcp_servers)}[/]" if self.mcp_servers else "")
            + (f"\nresuming session {self.session_id}" if self.session_id else "")
            + "\nType /help for commands",
            border_style="magenta"))
        while True:
            try:
                q = self.read_input()
            except (EOFError, KeyboardInterrupt):
                break
            if not q:
                continue
            try:
                if q.startswith("/") and self.handle_command(q):
                    continue
            except EOFError:
                break
            self.ask(q)
        readline.write_history_file(HISTORY_FILE)
        if self.session_id:
            console.print(f"\n[dim]Bye. To continue this session: cchat --resume {self.session_id}[/]")


def main():
    p = argparse.ArgumentParser(description="Lightweight terminal chat for Claude, powered by your own API key or gateway")
    p.add_argument("-c", "--config",
                   help=f"config file path (default: {USER_CONFIG}, then the script directory)")
    p.add_argument("-m", "--model", help="model alias or full ID")
    p.add_argument("-e", "--effort", choices=["low", "medium", "high", "xhigh", "max"],
                   help="effort level")
    p.add_argument("--mode",
                   help="permission mode (default acceptEdits: file edits are auto-approved)")
    p.add_argument("--allow", action="append",
                   help='extra allowed tool, repeatable: --allow "Bash(npm test)" --allow "Bash(python3 *)"')
    p.add_argument("--setting-sources",
                   help="settings layers claude reads (default project,local, i.e. skip ~/.claude/settings.json)")
    p.add_argument("-r", "--resume", help="resume a session by id")
    p.add_argument("-s", "--settings", help="extra settings.json path or JSON string")
    p.add_argument("prompt", nargs="*", help="ask once and exit (one-shot mode)")
    args = p.parse_args()

    config_path = args.config or find_config()
    try:
        cfg = load_config(config_path)
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"Failed to read config {config_path}: {e}")
    # Precedence: command-line args > config file > built-in defaults
    args.model = args.model or cfg.get("model")
    args.effort = args.effort or cfg.get("effort")
    args.setting_sources = args.setting_sources or cfg.get("setting_sources") or "project,local"
    args.mode = args.mode or cfg.get("permission_mode") or "acceptEdits"
    args.mcp_servers = cfg.get("mcp_servers") or {}
    if not isinstance(args.mcp_servers, dict):
        sys.exit(f"Invalid config {config_path}: mcp_servers must be an object, e.g. {{\"name\": {{\"command\": ...}}}}")
    if args.allow is None:
        args.allow = cfg.get("allowed_tools", ["Bash(git *)", "Bash(ls *)"])

    if not shutil.which("claude"):
        sys.exit("claude command not found; please install Claude Code first")

    cfg_env = config_env(cfg)
    try:
        settings_path = build_settings_file(cfg_env, args.settings)
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"Failed to read --settings {args.settings}: {e}")
    chat = Chat(args, cfg_env, settings_path)
    chat.config_path = config_path
    if args.prompt:
        chat.ask(" ".join(args.prompt))
    else:
        chat.run()


if __name__ == "__main__":
    main()
