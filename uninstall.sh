#!/bin/bash
# cchat cleanup: undo what setup.sh installed
#   Removed by default: the ~/.local/bin/cchat command, ~/.cchat_history input history
#   Asked one by one: rich in the uv cache, cchat.config.json (contains your key)
#   Never removed: uv, claude (the CLI still needs them), anything under ~/.claude (sessions, settings.json)
DIR="$(cd "$(dirname "$0")" && pwd)"
LINK="$HOME/.local/bin/cchat"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
ask() { read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy]$ ]]; }

echo "==> Removing the cchat command"
if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$DIR/cchat.py" ]; then
  rm "$LINK" && echo "    Removed $LINK"
elif [ -e "$LINK" ]; then
  echo "    $LINK was not installed from this directory, skipping"
else
  echo "    Not found, skipping"
fi

echo "==> Removing input history"
if [ -f "$HOME/.cchat_history" ]; then
  rm "$HOME/.cchat_history" && echo "    Removed ~/.cchat_history"
else
  echo "    Not found, skipping"
fi

echo "==> Optional cleanup"
if command -v uv >/dev/null && ask "    Remove rich from the uv cache? (other projects will re-download it if needed)"; then
  uv cache clean rich markdown-it-py mdurl pygments
fi
for CFG in "$HOME/.config/cchat/cchat.config.json" "$DIR/cchat.config.json"; do
  if [ -f "$CFG" ] && ask "    Delete $CFG? (it contains your key; this cannot be undone)"; then
    rm "$CFG" && echo "    Deleted"
  fi
done

echo
echo "✅ Cleanup done. Left untouched:"
echo "   - uv and claude themselves (to uninstall: brew uninstall uv / brew uninstall --cask claude-code)"
echo "   - ~/.claude/ (settings.json and session history, still used by the CLI)"
echo "   - the source in $DIR (to reinstall: ./setup.sh)"
