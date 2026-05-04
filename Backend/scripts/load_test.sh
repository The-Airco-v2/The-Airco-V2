#!/usr/bin/env bash
set -euo pipefail

# Multi-Camera Load Test for Airco Secure 2.0
# Tests 6 cameras simultaneously, checks GPU and latency

API_URL="${API_URL:-http://localhost:8000}"
SESSION_ID=""
SESSION_STOPPED=0

cleanup() {
    if [ -n "${SESSION_ID}" ] && [ "${SESSION_STOPPED}" -eq 0 ]; then
        echo -e "\nStopping session..."
        curl -sf -X POST "$API_URL/api/v2/sessions/$SESSION_ID/stop" > /dev/null || true
    fi
}

trap cleanup EXIT

echo "=== Airco Secure 2.0 — Multi-Camera Load Test ==="

# Get all cameras
CAMERAS=$(curl -sf "$API_URL/api/v2/cameras")
CAM_COUNT=$(echo "$CAMERAS" | python3 -c "import json, sys; print(len(json.load(sys.stdin)))")
echo "Available cameras: $CAM_COUNT"

if [ "$CAM_COUNT" -lt 6 ]; then
    echo "ERROR: Need 6 cameras for full load test, found $CAM_COUNT"
    exit 1
fi

# Get all camera IDs
CAM_IDS=$(echo "$CAMERAS" | python3 -c "
import json
import re
import sys

cams = json.load(sys.stdin)

def sort_key(camera):
    match = re.search(r'(\\d+)', camera.get('name', ''))
    return (int(match.group(1)) if match else 9999, camera.get('name', ''))

selected = []
seen_keys = set()
for camera in sorted(cams, key=sort_key):
    match = re.search(r'(\\d+)', camera.get('name', ''))
    camera_key = match.group(1) if match else camera['id']
    if camera_key in seen_keys:
        continue
    seen_keys.add(camera_key)
    selected.append(camera)
    if len(selected) == 6:
        break

print(json.dumps([c['id'] for c in selected]))
")
SELECTED_CAM_COUNT=$(echo "$CAM_IDS" | python3 -c "import json, sys; print(len(json.load(sys.stdin)))")

if [ "$SELECTED_CAM_COUNT" -lt 6 ]; then
    echo "ERROR: Need 6 distinct camera slots for full load test, found $SELECTED_CAM_COUNT"
    exit 1
fi

# Create session with all cameras
echo -e "\nCreating load test session with $SELECTED_CAM_COUNT cameras..."
SESSION=$(curl -sf -X POST "$API_URL/api/v2/sessions" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"Load Test - 6 Cameras\", \"mode\": \"live\", \"camera_ids\": $CAM_IDS}")
SESSION_ID=$(echo "$SESSION" | python3 -c "import json, sys; print(json.load(sys.stdin)['id'])")
echo "Session: $SESSION_ID"

# Start session
echo "Starting session..."
curl -sf -X POST "$API_URL/api/v2/sessions/$SESSION_ID/start" > /dev/null

# Monitor for 60 seconds
echo -e "\nMonitoring for 60 seconds..."
for i in $(seq 1 6); do
    sleep 10
    echo -e "\n--- ${i}0s checkpoint ---"

    # GPU utilization (if nvidia-smi available)
    if command -v nvidia-smi &> /dev/null; then
        GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null || echo "N/A")
        GPU_MEM=$(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null || echo "N/A")
        echo "  GPU Utilization: ${GPU_UTIL}%"
        echo "  GPU Memory: ${GPU_MEM} MiB"
    fi

    # API latency
    START_MS=$(date +%s%N)
    curl -sf "$API_URL/api/v2/sessions/$SESSION_ID/employee-intelligence" > /dev/null
    END_MS=$(date +%s%N)
    LATENCY_MS=$(((END_MS - START_MS) / 1000000))
    echo "  API Latency: ${LATENCY_MS}ms"

    # Person count
    INTEL=$(curl -sf "$API_URL/api/v2/sessions/$SESSION_ID/employee-intelligence")
    EMP_COUNT=$(echo "$INTEL" | python3 -c "import json, sys; print(len(json.load(sys.stdin).get('employees', [])))" 2>/dev/null || echo "0")
    echo "  Detected persons: $EMP_COUNT"
done

# Stop session
echo -e "\nStopping session..."
curl -sf -X POST "$API_URL/api/v2/sessions/$SESSION_ID/stop" > /dev/null
SESSION_STOPPED=1

# Final report
echo -e "\n=== Load Test Summary ==="
curl -sf "$API_URL/api/v2/reports/sessions/$SESSION_ID/summary" | python3 -m json.tool
echo -e "\n=== Load Test Complete ==="
