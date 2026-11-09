#!/bin/bash
# cchat one-step installer
#   1. Install uv if missing (brew if available, otherwise the official script)
#   2. Install claude if missing (same as above)
#   3. Create the ~/.local/bin/cchat command
#   4. Pre-download rich for Markdown / syntax highlighting (managed by uv via the header in cchat.py)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
has() { command -v "$1" >/dev/null 2>&1; }

echo "==> Checking uv"
if has uv; then
  echo "    Installed: $(uv --version)"
elif has brew; then
  brew install uv
else
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

echo "==> Checking claude"
if has claude; then
  echo "    Installed: $(claude --version)"
elif has brew; then
  brew install --cask claude-code
else
  curl -fsSL https://claude.ai/install.sh | bash
fi

echo "==> Installing the cchat command"
chmod +x "$DIR/cchat.py"
mkdir -p "$HOME/.local/bin"
ln -sf "$DIR/cchat.py" "$HOME/.local/bin/cchat"

echo "==> Downloading rich (syntax highlighting)"
uv run --script "$DIR/cchat.py" --help >/dev/null

echo
echo "✅ Done: cchat -> $DIR/cchat.py"
if [ ! -f "$DIR/cchat.config.json" ] && [ ! -f "$HOME/.config/cchat/cchat.config.json" ]; then
  echo "ℹ️  To use a custom gateway or API key, create a config:"
  echo "   mkdir -p ~/.config/cchat && cp $DIR/cchat.config.example.json ~/.config/cchat/cchat.config.json && chmod 600 ~/.config/cchat/cchat.config.json"
fi
case ":$(zsh -ic 'echo $PATH' 2>/dev/null):" in *":$HOME/.local/bin:"*) ;; *)
  echo '⚠️  ~/.local/bin is not in your PATH. Run:'
  echo '   echo '"'"'export PATH="$HOME/.local/bin:$PATH"'"'"' >> ~/.zshrc && source ~/.zshrc' ;;
esac
