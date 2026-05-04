#!/usr/bin/env bash
# gemini_mcp_dock.sh — Launch Gemini CLI with DockUP MCP server
# Usage: ./scripts/gemini_mcp_dock.sh "Your prompt here"
# Or without args for an interactive session

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKUP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load nvm
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [ -s "$NVM_DIR/nvm.sh" ]; then
  . "$NVM_DIR/nvm.sh"
  nvm use 22 --silent 2>/dev/null || true
fi

# Verify node version >= 20
NODE_MAJOR=$(node --version 2>/dev/null | sed 's/^v//' | cut -d. -f1)
if [ "${NODE_MAJOR:-0}" -lt 20 ]; then
  echo "ERROR: Node.js >= 20 required (current: $(node --version 2>/dev/null || echo 'not found'))"
  echo "Run: nvm install 22 && nvm use 22"
  exit 1
fi

# Set Gemini API key from file if not in environment
if [ -z "${GEMINI_API_KEY:-}" ]; then
  for KEY_FILE in "$DOCKUP_ROOT/../gemini_api" "$DOCKUP_ROOT/../gemini api"; do
    if [ -f "$KEY_FILE" ]; then
      export GEMINI_API_KEY="$(cat "$KEY_FILE" | tr -d '[:space:]')"
      break
    fi
  done
fi

if [ -z "${GEMINI_API_KEY:-}" ]; then
  echo "ERROR: GEMINI_API_KEY not set and no key file found"
  exit 1
fi

# Unset GOOGLE_API_KEY to avoid conflict warning
unset GOOGLE_API_KEY 2>/dev/null || true

# Trust workspace for headless use
export GEMINI_CLI_TRUST_WORKSPACE=true

# Verify Gemini CLI
if ! command -v gemini &>/dev/null; then
  echo "ERROR: Gemini CLI not found. Install with: npm install -g @google/gemini-cli"
  exit 1
fi

echo "✓ Node $(node --version) | Gemini CLI $(gemini --version 2>&1 | head -1)"
echo "✓ Working directory: $DOCKUP_ROOT"
echo "✓ MCP server: dockup-control"
echo ""

if [ $# -gt 0 ]; then
  # Prompt mode
  PROMPT="$*"
  echo "→ Sending prompt: $PROMPT"
  echo ""
  exec gemini --skip-trust \
    --allowed-mcp-server-names dockup-control \
    -p "$PROMPT"
else
  # Interactive mode
  echo "→ Starting interactive Gemini CLI with DockUP MCP..."
  echo ""
  exec gemini --skip-trust \
    --allowed-mcp-server-names dockup-control
fi
