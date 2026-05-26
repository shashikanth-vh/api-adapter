#!/bin/bash
# ============================================================
# vnf_pm_report_fetch.sh
# Fetch a VNF Performance Management (PM) Report via Tacker v2 API
# Endpoint: GET /vnfpm/v2/pm_jobs/{pmJobId}/reports/{reportId}
# ============================================================

CONFIG_FILE="./pm_report_config.conf"

# -------------------------------------------------------
# 1. Validate config file exists
# -------------------------------------------------------
if [ ! -f "$CONFIG_FILE" ]; then
  echo "[ERROR] Config file not found: $CONFIG_FILE"
  exit 1
fi

# -------------------------------------------------------
# 2. Load config parameters
# -------------------------------------------------------
while IFS='=' read -r key value; do
  [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
  key=$(echo "$key" | xargs)
  value=$(echo "$value" | xargs)
  declare "$key=$value"
done < "$CONFIG_FILE"

# -------------------------------------------------------
# 3. Validate mandatory parameters
# -------------------------------------------------------
MISSING_PARAMS=0

if [ -z "$PM_JOB_ID" ]; then
  echo "[ERROR] PM_JOB_ID is not set in $CONFIG_FILE."
  MISSING_PARAMS=1
fi

if [ -z "$REPORT_ID" ]; then
  echo "[ERROR] REPORT_ID is not set in $CONFIG_FILE."
  MISSING_PARAMS=1
fi

if [ "$MISSING_PARAMS" -eq 1 ]; then
  echo ""
  echo "  How to find these values:"
  echo "  ─────────────────────────"
  echo "  PM_JOB_ID  → From the PM Job Create response (field: id)"
  echo "               OR from the notification body (field: pmJobId)"
  echo ""
  echo "  REPORT_ID  → From the PerformanceInformationAvailableNotification:"
  echo "               _links.performanceReport.href (last path segment)"
  echo "               e.g. .../pm_jobs/<pmJobId>/reports/<reportId>"
  exit 1
fi

# -------------------------------------------------------
# 4. Determine output filename
# -------------------------------------------------------
if [ -z "$REPORT_OUTPUT_FILE" ]; then
  REPORT_OUTPUT_FILE="pm_report_${PM_JOB_ID}_${REPORT_ID}.json"
fi

echo "============================================================"
echo " VNF PM Report Fetch"
echo "============================================================"
echo " Host        : $PROTOCOL://$HOST:$PORT"
echo " API Version : $API_VERSION"
echo " PM Job ID   : $PM_JOB_ID"
echo " Report ID   : $REPORT_ID"
echo " Save to file: $SAVE_REPORT_TO_FILE"
if [ "$SAVE_REPORT_TO_FILE" == "true" ]; then
  echo " Output file : $REPORT_OUTPUT_FILE"
fi
echo "============================================================"

# -------------------------------------------------------
# 5. Obtain Keystone Auth Token
# -------------------------------------------------------
AUTH_PAYLOAD=$(cat <<EOF
{
  "auth": {
    "identity": {
      "methods": ["password"],
      "password": {
        "user": {
          "name": "${USERNAME}",
          "domain": { "id": "${USER_DOMAIN_ID}" },
          "password": "${PASSWORD}"
        }
      }
    },
    "scope": {
      "project": {
        "name": "${PROJECT_NAME}",
        "domain": { "id": "${PROJECT_DOMAIN_ID}" }
      }
    }
  }
}
EOF
)

echo ""
echo "🔐 Requesting Keystone token from: $KEYSTONE_URL"

TOKEN=$(curl -s -i -X POST "${KEYSTONE_URL}" \
  -H "Content-Type: application/json" \
  --data "${AUTH_PAYLOAD}" | awk '/^X-Subject-Token:/ {print $2}' | tr -d '\r')

if [ -z "$TOKEN" ]; then
  echo "[ERROR] Failed to retrieve Keystone token. Check credentials and KEYSTONE_URL."
  exit 1
fi

echo "[OK] Keystone token retrieved successfully."

# -------------------------------------------------------
# 6. Send PM Report Fetch Request
# -------------------------------------------------------
REPORT_URL="${PROTOCOL}://${HOST}:${PORT}/vnfpm/${API_VERSION}/pm_jobs/${PM_JOB_ID}/reports/${REPORT_ID}"

echo ""
echo "📡 Fetching PM Report from: $REPORT_URL"
echo ""

RESPONSE=$(curl -g -i -s -X GET "${REPORT_URL}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "User-Agent: ${USER_AGENT}" \
  -H "Version: ${API_VERSION_HEADER}" \
  -H "X-Auth-Token: ${TOKEN}")

# -------------------------------------------------------
# 7. Parse Response
# -------------------------------------------------------
HTTP_STATUS=$(echo "$RESPONSE" | awk '/^HTTP\// {print $2}' | tail -1)
BODY=$(echo "$RESPONSE" | awk '/^\r?$/{found=1; next} found{print}')

echo "--- Response (HTTP $HTTP_STATUS) ---"
echo "$BODY"
echo "------------------------------------"
echo ""

if [ "$HTTP_STATUS" == "200" ]; then
  echo "✅ PM Report fetched successfully (HTTP 200)."

  # -------------------------------------------------------
  # 8. Parse and Display Key Report Fields
  # -------------------------------------------------------
  echo ""
  echo "┌─────────────────────────────────────────────────────────┐"
  echo "│  PM Report Summary"
  echo "│"

  # Extract report id
  R_ID=$(echo "$BODY" | grep -o '"id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/"id"[[:space:]]*:[[:space:]]*"\([^"]*\)"/\1/')
  [ -n "$R_ID" ] && echo "│  Report ID      : $R_ID"

  # Extract jobId
  JOB_ID=$(echo "$BODY" | grep -o '"jobId"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/"jobId"[[:space:]]*:[[:space:]]*"\([^"]*\)"/\1/')
  [ -n "$JOB_ID" ] && echo "│  PM Job ID      : $JOB_ID"

  # Extract entries array count (number of metric data points)
  ENTRY_COUNT=$(echo "$BODY" | grep -o '"entries"' | wc -l | xargs)
  [ -n "$ENTRY_COUNT" ] && echo "│  Entries found  : $ENTRY_COUNT"

  # Extract performanceMetric values from entries
  METRICS=$(echo "$BODY" | grep -o '"performanceMetric"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/"performanceMetric"[[:space:]]*:[[:space:]]*"\([^"]*\)"/\1/' | sort -u)
  if [ -n "$METRICS" ]; then
    echo "│  Metrics        :"
    while IFS= read -r metric; do
      echo "│    - $metric"
    done <<< "$METRICS"
  fi

  # Extract startTime / stopTime from first performanceValues entry
  START_TIME=$(echo "$BODY" | grep -o '"startTime"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/"startTime"[[:space:]]*:[[:space:]]*"\([^"]*\)"/\1/')
  STOP_TIME=$(echo "$BODY" | grep -o '"stopTime"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/"stopTime"[[:space:]]*:[[:space:]]*"\([^"]*\)"/\1/')
  [ -n "$START_TIME" ] && echo "│  Start Time     : $START_TIME"
  [ -n "$STOP_TIME"  ] && echo "│  Stop Time      : $STOP_TIME"

  echo "│"
  echo "└─────────────────────────────────────────────────────────┘"

  # -------------------------------------------------------
  # 9. Optionally Save Report to File
  # -------------------------------------------------------
  if [ "$SAVE_REPORT_TO_FILE" == "true" ]; then
    echo ""
    echo "$BODY" > "$REPORT_OUTPUT_FILE"
    if [ $? -eq 0 ]; then
      echo "💾 Report saved to: $REPORT_OUTPUT_FILE"
    else
      echo "[WARN] Failed to write report to file: $REPORT_OUTPUT_FILE"
    fi
  fi

else
  echo "❌ PM Report fetch failed (HTTP $HTTP_STATUS)."
  case "$HTTP_STATUS" in
    400) echo "   Bad Request — Check PM_JOB_ID and REPORT_ID format." ;;
    401) echo "   Unauthorized — Keystone token was rejected. Verify credentials." ;;
    404) echo "   Not Found — PM Job or Report does not exist. Verify IDs." ;;
    406) echo "   Not Acceptable — Accept header issue. Check API_VERSION_HEADER." ;;
    *)   echo "   Review the response body above for further details." ;;
  esac
  exit 1
fi
