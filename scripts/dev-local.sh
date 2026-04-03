#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# NarraNexus — Local Development Server (tmux)
# Starts all services in a tmux session with separate windows.
# Window 0: Control panel (status + quit)
# Window 1-5: Individual services
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SESSION="nexus-dev"

# --- Platform-aware SQLite path ---
case "$(uname -s)" in
  Darwin) DB_DIR="$HOME/Library/Application Support/NarraNexus" ;;
  *)      DB_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/NarraNexus" ;;
esac
mkdir -p "$DB_DIR"
export DATABASE_URL="sqlite:///$DB_DIR/nexus.db"

# --- Check tmux ---
if ! command -v tmux &>/dev/null; then
  echo "tmux is required. Install: brew install tmux (macOS) or apt install tmux (Linux)"
  exit 1
fi

# --- Kill existing session if running ---
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Stopping existing session '$SESSION'..."
  tmux kill-session -t "$SESSION"
  sleep 1
fi

# --- Banner ---
C="\033[36m"; G="\033[32m"; Y="\033[33m"; R="\033[0m"
echo ""
echo -e "${C}  _   _                    _   _                    ${R}"
echo -e "${C} | \\ | | __ _ _ __ _ __ __|  \\| | _____  ___   _ ___${R}"
echo -e "${C} |  \\| |/ _\` | '__| '__/ _\` | |\` |/ _ \\\\ \\\\/ / | | / __|${R}"
echo -e "${C} | |\\\\ | (_| | |  | | | (_| | |\\\\ |  __/>  <| |_| \\\\__ \\\\${R}"
echo -e "${C} |_| \\\\_|\\\\__,_|_|  |_|  \\\\__,_|_| \\\\_|\\\\___/_/\\\\_\\\\\\\\__,_|___/${R}"
echo ""
echo -e "  ${G}Local Development Server${R}"
echo -e "  Database: ${Y}$DATABASE_URL${R}"
echo ""

# --- Common env ---
ENV_CMD="export DATABASE_URL='$DATABASE_URL'; cd '$PROJECT_ROOT'"

# --- Create control script ---
CONTROL_SCRIPT="$PROJECT_ROOT/scripts/.control.sh"
cat > "$CONTROL_SCRIPT" << 'CTRL'
#!/usr/bin/env bash
SESSION="nexus-dev"
C="\033[36m"; G="\033[32m"; Y="\033[33m"; RED="\033[31m"; R="\033[0m"

check_port() {
  if lsof -iTCP:"$1" -sTCP:LISTEN -P -n &>/dev/null 2>&1; then
    echo -e "  ${G}●${R} $2 (port $1)"
  else
    echo -e "  ${RED}○${R} $2 (port $1)"
  fi
}

while true; do
  clear
  echo ""
  echo -e "${C}  ╔═══════════════════════════════════════╗${R}"
  echo -e "${C}  ║       NarraNexus Control Panel        ║${R}"
  echo -e "${C}  ╚═══════════════════════════════════════╝${R}"
  echo ""
  echo -e "  ${Y}Service Status${R}"
  echo ""
  check_port 8000 "Backend API"
  check_port 5173 "Frontend   "
  check_port 5174 "Frontend   "

  # Check non-port services by process
  if pgrep -f "module_runner.py mcp" >/dev/null 2>&1; then
    echo -e "  ${G}●${R} MCP Server"
  else
    echo -e "  ${RED}○${R} MCP Server"
  fi

  if pgrep -f "module_poller" >/dev/null 2>&1; then
    echo -e "  ${G}●${R} Module Poller"
  else
    echo -e "  ${RED}○${R} Module Poller"
  fi

  if pgrep -f "job_trigger" >/dev/null 2>&1; then
    echo -e "  ${G}●${R} Job Trigger"
  else
    echo -e "  ${RED}○${R} Job Trigger"
  fi

  echo ""
  echo -e "  ${Y}Windows${R}"
  echo ""
  echo -e "  ${C}Ctrl+B N${R}  Next window"
  echo -e "  ${C}Ctrl+B P${R}  Previous window"
  echo -e "  ${C}Ctrl+B 1${R}  Backend"
  echo -e "  ${C}Ctrl+B 2${R}  MCP"
  echo -e "  ${C}Ctrl+B 3${R}  Poller"
  echo -e "  ${C}Ctrl+B 4${R}  Jobs"
  echo -e "  ${C}Ctrl+B 5${R}  Frontend"
  echo -e "  ${C}Ctrl+B D${R}  Detach (keep running)"
  echo ""
  echo -e "  Press ${RED}q${R} to stop all services and exit"
  echo ""

  # Wait for input with timeout (refresh every 3s)
  if read -t 3 -n 1 key 2>/dev/null; then
    if [ "$key" = "q" ] || [ "$key" = "Q" ]; then
      echo ""
      echo -e "  ${Y}Stopping all services...${R}"
      tmux kill-session -t "$SESSION" 2>/dev/null
      echo -e "  ${G}All services stopped.${R}"
      exit 0
    fi
  fi
done
CTRL
chmod +x "$CONTROL_SCRIPT"

# --- Create tmux session with Control window ---
tmux new-session -d -s "$SESSION" -n "Control" \
  "bash '$CONTROL_SCRIPT'"

# --- Backend ---
tmux new-window -t "$SESSION" -n "Backend" \
  "$ENV_CMD; echo '=== Backend API :8000 ==='; uv run uvicorn backend.main:app --port 8000; echo 'Backend stopped. Press Enter to close.'; read"

# --- MCP Server ---
tmux new-window -t "$SESSION" -n "MCP" \
  "$ENV_CMD; echo '=== MCP Server ==='; uv run python src/xyz_agent_context/module/module_runner.py mcp; echo 'MCP stopped. Press Enter to close.'; read"

# --- Module Poller ---
tmux new-window -t "$SESSION" -n "Poller" \
  "$ENV_CMD; echo '=== Module Poller ==='; uv run python -m xyz_agent_context.services.module_poller; echo 'Poller stopped. Press Enter to close.'; read"

# --- Job Trigger ---
tmux new-window -t "$SESSION" -n "Jobs" \
  "$ENV_CMD; echo '=== Job Trigger ==='; uv run python src/xyz_agent_context/module/job_module/job_trigger.py; echo 'Jobs stopped. Press Enter to close.'; read"

# --- Frontend ---
tmux new-window -t "$SESSION" -n "Frontend" \
  "cd '$PROJECT_ROOT/frontend'; echo '=== Frontend Dev Server ==='; npm run dev; echo 'Frontend stopped. Press Enter to close.'; read"

# --- Select Control window ---
tmux select-window -t "$SESSION:Control"

echo -e "${G}All services started in tmux session '${SESSION}'.${R}"
echo ""
echo -e "  Frontend:  ${C}http://localhost:5173${R}"
echo -e "  Backend:   ${C}http://localhost:8000${R}"
echo ""

# --- Attach ---
tmux attach -t "$SESSION"
