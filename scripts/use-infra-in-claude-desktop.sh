#!/bin/bash
# Register the gateway as the single "infra" entry in Claude Desktop and remove the
# entries it replaces (pass their names as arguments).
# Do not run it while Claude Desktop is open: the app writes its in-memory settings
# back to the file when it quits, so the change would be lost.
# Usage: quit Claude Desktop (Cmd+Q), run  scripts/use-infra-in-claude-desktop.sh [name ...],
# then start Claude Desktop again.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${CLAUDE_DESKTOP_CONFIG:-$HOME/Library/Application Support/Claude/claude_desktop_config.json}"
GATEWAY="$ROOT/.venv/bin/infra-mcp"

# pgrep cannot see the app's arguments here, so match on ps output instead.
# No grep -q: its early exit would SIGPIPE ps and pipefail would read that as "not running".
if [ -z "${CLAUDE_DESKTOP_CONFIG:-}" ] && ps -axo args= | grep '^/Applications/Claude.app/Contents/MacOS/Claude$' >/dev/null; then
  echo "Claude Desktop is still running. Quit it with Cmd+Q, then run this again." >&2
  exit 1
fi
[ -x "$GATEWAY" ] || { echo "Gateway not found: $GATEWAY" >&2; exit 1; }
[ -f "$CONFIG" ] || { mkdir -p "$(dirname "$CONFIG")"; echo '{}' > "$CONFIG"; }

BACKUP="$ROOT/backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP" && chmod 700 "$ROOT/backups" "$BACKUP"
cp -p "$CONFIG" "$BACKUP/claude_desktop_config.json"

/usr/bin/python3 - "$CONFIG" "$GATEWAY" "$@" <<'EOF'
import json, os, sys, tempfile
path, gateway, replaced = sys.argv[1], sys.argv[2], sys.argv[3:]
cfg = json.load(open(path))
servers = cfg.setdefault("mcpServers", {})
removed = [name for name in replaced if servers.pop(name, None) is not None]
servers["infra"] = {"command": gateway}
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
os.chmod(tmp, os.stat(path).st_mode & 0o777)
os.replace(tmp, path)
print("removed:", removed or "-", "| mcpServers now:", list(servers))
EOF
echo "Backup: $BACKUP/claude_desktop_config.json"
echo "You can start Claude Desktop now."
