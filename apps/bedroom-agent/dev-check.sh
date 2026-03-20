#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

# Load env
if [[ -f "$ENV_FILE" ]]; then
  export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

HA_URL="${HA_BASE_URL:-http://localhost:8123}"
HA_URL="${HA_URL%/}"  # strip trailing slash
HA_HDR="Authorization: Bearer ${HA_TOKEN:-}"

echo "=== [1/4] Reloading HA automations + scripts ==="
curl -sf -X POST "$HA_URL/api/services/automation/reload" \
  -H "$HA_HDR" -H "Content-Type: application/json" -d '{}' > /dev/null && echo "  automations reloaded"
curl -sf -X POST "$HA_URL/api/services/script/reload" \
  -H "$HA_HDR" -H "Content-Type: application/json" -d '{}' > /dev/null && echo "  scripts reloaded"

echo "=== [2/4] Restarting bedroom-agent (local uvicorn) ==="
pkill -f "uvicorn src.app:app" 2>/dev/null || true
# Wait until port 9000 is free (up to 5s)
for i in $(seq 1 5); do
  ss -tlnp 'sport = :9000' 2>/dev/null | grep -q LISTEN || break
  sleep 1
done

source "$SCRIPT_DIR/.venv/bin/activate"
cd "$SCRIPT_DIR"
TOOL_BACKEND=local VISION_ANALYSIS_ENABLED=false \
  uvicorn src.app:app --host 0.0.0.0 --port 9000 --reload \
  > /tmp/uvicorn-dev.log 2>&1 &
UVICORN_PID=$!
echo "  started PID $UVICORN_PID"

echo "=== [3/4] Waiting for startup ==="
for i in $(seq 1 15); do
  if curl -sf http://localhost:9000/health > /dev/null 2>&1; then
    echo "  health OK"
    break
  fi
  sleep 1
  if [[ $i -eq 15 ]]; then
    echo "  ERROR: agent did not start in 15s"
    cat /tmp/uvicorn-dev.log
    exit 1
  fi
done

READYZ=$(curl -sf http://localhost:9000/readyz 2>/dev/null || echo '{}')
echo "  readyz: $READYZ"

echo "=== [4/4] Running tests ==="
./.venv/bin/pytest tests -q
