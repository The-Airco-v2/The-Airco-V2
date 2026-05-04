#!/usr/bin/env bash
set -euo pipefail

# End-to-End Replay Test for Airco Secure 2.0
# Prerequisites: Docker Compose stack running, database migrated

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_URL="${API_URL:-http://localhost:8000}"
REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
SESSION_ID=""
SESSION_STOPPED=0

cleanup() {
    if [ -n "${SESSION_ID}" ] && [ "${SESSION_STOPPED}" -eq 0 ]; then
        echo -e "\nStopping session..."
        curl -sf -X POST "$API_URL/api/v2/sessions/$SESSION_ID/stop" | python3 -m json.tool || true
    fi
}

trap cleanup EXIT

echo "=== Airco Secure 2.0 — E2E Replay Test ==="

# Step 1: Health check
echo -e "\n[1/6] Health check..."
HEALTH=$(curl -sf "$API_URL/health" || echo '{"status":"down"}')
echo "  API: $HEALTH"

# Step 2: Seed cameras
echo -e "\n[2/6] Seeding cameras..."
python3 "$SCRIPT_DIR/seed_cameras.py"

# Step 3: Create session
echo -e "\n[3/6] Creating replay session..."
CAMERAS=$(curl -sf "$API_URL/api/v2/cameras")
CAM_ID=$(echo "$CAMERAS" | python3 -c "
import json
import re
import sys

cams = json.load(sys.stdin)

def sort_key(camera):
    match = re.search(r'(\\d+)', camera.get('name', ''))
    return (int(match.group(1)) if match else 9999, camera.get('name', ''))

replay_cameras = [camera for camera in cams if '/replay/' in camera.get('rtsp_url', '')]
selected = sorted(replay_cameras or cams, key=sort_key)
print(selected[0]['id'] if selected else '')
" 2>/dev/null || echo "")
if [ -z "$CAM_ID" ]; then
    echo "  ERROR: No cameras found. Seed cameras first."
    exit 1
fi

SESSION=$(curl -sf -X POST "$API_URL/api/v2/sessions" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"E2E Replay Test\", \"mode\": \"replay\", \"camera_ids\": [\"$CAM_ID\"]}")
SESSION_ID=$(echo "$SESSION" | python3 -c "import json, sys; print(json.load(sys.stdin)['id'])")
echo "  Session: $SESSION_ID"

# Step 4: Start session
echo -e "\n[4/6] Starting session..."
curl -sf -X POST "$API_URL/api/v2/sessions/$SESSION_ID/start" | python3 -m json.tool

# Step 5: Wait and verify Redis streams
echo -e "\n[5/6] Waiting 10s for events to flow..."
sleep 10

echo "  Redis stream lengths:"
for stream in airco:tracks airco:crops airco:identity airco:phones airco:snapshots airco:alerts; do
    LEN=$(redis-cli -u "$REDIS_URL" XLEN "$stream" 2>/dev/null || echo "N/A")
    echo "    $stream: $LEN"
done

# Step 6: Check Employee Intelligence endpoint
echo -e "\n[6/6] Employee Intelligence check..."
INTEL=$(curl -sf "$API_URL/api/v2/sessions/$SESSION_ID/employee-intelligence")
EMP_COUNT=$(echo "$INTEL" | python3 -c "import json, sys; print(len(json.load(sys.stdin).get('employees', [])))" 2>/dev/null || echo "0")
echo "  Detected persons: $EMP_COUNT"

# Stop session
echo -e "\nStopping session..."
curl -sf -X POST "$API_URL/api/v2/sessions/$SESSION_ID/stop" | python3 -m json.tool
SESSION_STOPPED=1

# Summary
echo -e "\n=== Report Summary ==="
curl -sf "$API_URL/api/v2/reports/sessions/$SESSION_ID/summary" | python3 -m json.tool

echo -e "\n=== E2E Replay Test Complete ==="
