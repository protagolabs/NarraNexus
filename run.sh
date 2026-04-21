#!/usr/bin/env bash
# ============================================================================
#   _   _                    _   _
#  | \ | | __ _ _ __ _ __ __|  \| | _____  ___   _ ___
#  |  \| |/ _` | '__| '__/ _` | |` |/ _ \ \/ / | | / __|
#  | |\ | (_| | |  | | | (_| | |\ |  __/>  <| |_| \__ \
#  |_| \_|\__,_|_|  |_|  \__,_|_| \_|\___/_/\_\\__,_|___/
#
#  NarraNexus — Intelligent Agent Platform
# ============================================================================
#
#  Usage:
#    bash run.sh          Start all services (backend + frontend)
#    bash run.sh stop     Stop all NarraNexus processes
#    bash run.sh status   Show service status
#    bash run.sh build    Build desktop app (DMG)
#
# ============================================================================

set -uo pipefail

# Clear any external VIRTUAL_ENV (e.g. pyenv) that interferes with uv's .venv
unset VIRTUAL_ENV 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
C="\033[36m"; G="\033[32m"; Y="\033[33m"; R="\033[0m"; RED="\033[31m"

# --- Helpers ---

status() {
  echo ""
  echo -e "${C}Service Status${R}"
  echo ""
  local services=("8100:DB Proxy" "8000:Backend API" "5173:Frontend" "7801:MCP Server" "7830:Lark Trigger")
  for entry in "${services[@]}"; do
    local port="${entry%%:*}"
    local name="${entry#*:}"
    if lsof -iTCP:"$port" -sTCP:LISTEN -P -n &>/dev/null 2>&1 || \
       ss -tlnp 2>/dev/null | grep -q ":${port} "; then
      echo -e "  ${G}●${R} ${name} (port ${port})"
    else
      echo -e "  ${RED}○${R} ${name} (port ${port})"
    fi
  done
  echo ""
}

stop_all() {
  echo -e "${Y}Stopping NarraNexus services...${R}"
  # Kill tmux session if running
  tmux kill-session -t nexus-dev 2>/dev/null || true
  # Kill processes on known ports
  for port in 8100 8000 5173 5174 7801 7802 7803 7804 7805 7830; do
    lsof -ti:"$port" 2>/dev/null | xargs kill -9 2>/dev/null || true
  done
  # Kill known process patterns
  pkill -f "sqlite_proxy_server" 2>/dev/null || true
  pkill -f "uvicorn backend.main:app" 2>/dev/null || true
  pkill -f "module_runner.py mcp" 2>/dev/null || true
  pkill -f "module_poller" 2>/dev/null || true
  pkill -f "job_trigger" 2>/dev/null || true
  pkill -f "message_bus_trigger" 2>/dev/null || true
  pkill -f "run_lark_trigger" 2>/dev/null || true
  echo -e "${G}All services stopped.${R}"
}

check_deps() {
  local missing=()
  command -v uv  &>/dev/null || missing+=("uv")
  command -v node &>/dev/null || missing+=("node")
  if [ ${#missing[@]} -gt 0 ]; then
    echo -e "${RED}Missing required dependencies: ${missing[*]}${R}"
    echo ""
    echo "  Install uv:   curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "  Install node:  https://nodejs.org/"
    echo ""
    exit 1
  fi

  # Install or update lark-cli (optional — only needed for Lark/Feishu).
  # The previous version silently piped `npm install` output to tail and had
  # no timeout, so a slow npm registry, EACCES on a system-wide install, or
  # blocked network would hang "Installing lark-cli..." forever with no
  # feedback. Three safeties now:
  #   1. Hard timeout (120s) so we never wedge startup.
  #   2. Output streams live so the user sees progress, not a frozen line.
  #   3. If install fails/times out we warn and continue — Lark features
  #      degrade gracefully; the rest of NarraNexus still works.
  _LARK_CLI_MIN="1.0.12"
  _LARK_CLI_TIMEOUT=120

  _try_install_lark_cli() {
    local action="$1"  # "Installing" or "Updating" (display label, pre-capitalized
    # — avoids bash 3.2 lacking ${var^})
    echo -e "${Y}${action} lark-cli (timeout ${_LARK_CLI_TIMEOUT}s)...${R}"
    # Use a subshell + background + wait-with-timeout pattern. `timeout`
    # isn't on stock macOS; this works everywhere with just sh primitives.
    (npm install -g @larksuite/cli) &
    local npm_pid=$!
    local elapsed=0
    while kill -0 "$npm_pid" 2>/dev/null; do
      if [ "$elapsed" -ge "$_LARK_CLI_TIMEOUT" ]; then
        echo -e "${RED}npm install hung > ${_LARK_CLI_TIMEOUT}s — killing.${R}"
        kill -9 "$npm_pid" 2>/dev/null
        wait "$npm_pid" 2>/dev/null
        return 124
      fi
      sleep 1
      elapsed=$((elapsed + 1))
    done
    wait "$npm_pid"
    return $?
  }

  _warn_lark_skipped() {
    echo -e "${Y}⚠ lark-cli not available — Lark/Feishu features will be disabled.${R}"
    echo "  Common causes + fixes:"
    echo "    • Slow registry (China users): npm config set registry https://registry.npmmirror.com"
    echo "    • Permission denied: use nvm (https://github.com/nvm-sh/nvm) or sudo"
    echo "    • Network blocked: check your connection to registry.npmjs.org"
    echo "  Then retry: npm install -g @larksuite/cli"
    echo ""
  }

  if ! command -v lark-cli &>/dev/null; then
    if ! _try_install_lark_cli "Installing"; then
      _warn_lark_skipped
    fi
  else
    _lark_ver=$(lark-cli --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "0.0.0")
    if [ "$(printf '%s\n' "${_LARK_CLI_MIN}" "$_lark_ver" | sort -V | head -1)" != "${_LARK_CLI_MIN}" ]; then
      if ! _try_install_lark_cli "Updating"; then
        echo -e "${Y}⚠ lark-cli update failed; continuing with ${_lark_ver}.${R}"
      fi
    fi
  fi

  # Post-install / post-upgrade verification. npm can "succeed" but leave
  # the binary outside $PATH (classic ~/.npm-global/bin not exported case).
  if ! command -v lark-cli &>/dev/null; then
    _npm_prefix=$(npm config get prefix 2>/dev/null || echo "")
    if [ -n "$_npm_prefix" ] && [ -x "$_npm_prefix/bin/lark-cli" ]; then
      echo -e "${Y}lark-cli installed at $_npm_prefix/bin but missing from \$PATH.${R}"
      echo "  Add to your shell rc: export PATH=\"$_npm_prefix/bin:\$PATH\""
      echo ""
    fi
  fi

  # Install Lark CLI Skills (Agent knowledge docs for lark-cli usage)
  if ! ls ~/.claude/skills/lark-shared/SKILL.md &>/dev/null 2>&1; then
    echo -e "${Y}Installing Lark CLI Skills...${R}"
    npx skills add larksuite/cli -y -g 2>&1 | tail -1
  fi

  # Check Python version (>=3.13 required)
  local py_version
  py_version=$(uv python find 2>/dev/null | xargs -I{} {} -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "")
  if [ -n "$py_version" ]; then
    local major minor
    major="${py_version%%.*}"
    minor="${py_version#*.}"
    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 13 ]; }; then
      echo -e "${RED}Python >= 3.13 is required (found $py_version).${R}"
      echo "  Install: uv python install 3.13"
      exit 1
    fi
  fi

  # Optional: lark-cli (only needed for Lark/Feishu integration)
  if ! command -v lark-cli &>/dev/null; then
    echo -e "${Y}Note: lark-cli not found. Lark/Feishu features will not work.${R}"
    echo -e "  Install: ${C}npm install -g @larksuite/cli${R}"
    echo ""
  fi
}

# --- Main ---

case "${1:-}" in
  stop)
    stop_all
    ;;
  status)
    status
    ;;
  build)
    exec "$SCRIPT_DIR/scripts/build-desktop.sh"
    ;;
  *)
    check_deps

    # Install frontend deps if needed
    if [ ! -d "$SCRIPT_DIR/frontend/node_modules" ]; then
      echo -e "${Y}Installing frontend dependencies...${R}"
      (cd "$SCRIPT_DIR/frontend" && npm ci)
    fi

    # Sync Python deps — clear ALL external Python env vars that interfere with uv
    UV_CLEAN_ENV="env -u VIRTUAL_ENV -u CONDA_PREFIX -u CONDA_DEFAULT_ENV -u CONDA_PYTHON_EXE"
    echo -e "${Y}Syncing Python dependencies...${R}"
    $UV_CLEAN_ENV uv sync 2>&1 | tail -1
    # Ensure editable install is active (uv .pth can fail on some Python builds)
    $UV_CLEAN_ENV uv pip install -e "$SCRIPT_DIR" --python "$SCRIPT_DIR/.venv/bin/python3" --reinstall-package xyz-agent-context 2>&1 | tail -1
    # Verify import works
    "$SCRIPT_DIR/.venv/bin/python3" -c "import xyz_agent_context" 2>/dev/null || {
      echo -e "${RED}Failed to install xyz_agent_context. Rebuilding venv...${R}"
      rm -rf "$SCRIPT_DIR/.venv"
      $UV_CLEAN_ENV uv sync 2>&1 | tail -1
      $UV_CLEAN_ENV uv pip install -e "$SCRIPT_DIR" --python "$SCRIPT_DIR/.venv/bin/python3" 2>&1 | tail -1
    }

    # Start everything
    exec "$SCRIPT_DIR/scripts/dev-local.sh"
    ;;
esac
