#!/bin/bash
# ============================================================
# vnf_pm_job_create.sh
# Create a VNF Performance Management (PM) Job via Tacker v2 API
# ============================================================

CONFIG_FILE="./pm_job_config.conf"

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

echo "============================================================"
echo " VNF PM Job Create"
echo "============================================================"
echo " Host              : $PROTOCOL://$HOST:$PORT"
echo " VNF Instance ID   : $VNF_INSTANCE_ID"
echo " Object Type       : $OBJECT_TYPE"
echo " Metric            : $PERFORMANCE_METRIC"
echo " Metric Group      : $PERFORMANCE_METRIC_GROUP"
echo " Collection Period : ${COLLECTION_PERIOD}s"
echo " Reporting Period  : ${REPORTING_PERIOD}s"
echo " Reporting Boundary: $REPORTING_BOUNDARY"
echo " Callback URI      : $CALLBACK_URI"
echo " Prometheus Host   : $PROMETHEUS_HOST"
echo "============================================================"

# -------------------------------------------------------
# 3. Obtain Keystone Auth Token
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
# 4. Build PM Job JSON Payload
# -------------------------------------------------------
PM_JOB_PAYLOAD=$(cat <<EOF
{
    "objectType": "${OBJECT_TYPE}",
    "objectInstanceIds": [
        "${VNF_INSTANCE_ID}"
    ],
    "subObjectInstanceIds": [],
    "criteria": {
        "performanceMetric": [
            "${PERFORMANCE_METRIC}"
        ],
        "performanceMetricGroup": [
            "${PERFORMANCE_METRIC_GROUP}"
        ],
        "collectionPeriod": ${COLLECTION_PERIOD},
        "reportingPeriod": ${REPORTING_PERIOD},
        "reportingBoundary": "${REPORTING_BOUNDARY}"
    },
    "callbackUri": "${CALLBACK_URI}",
    "authentication": {
        "authType": [
            "${AUTH_TYPE}"
        ],
        "paramsBasic": {
            "userName": "${CALLBACK_USERNAME}",
            "password": "${CALLBACK_PASSWORD}"
        }
    },
    "metadata": {
        "monitoring": {
            "monitorName": "${MONITOR_NAME}",
            "driverType": "${DRIVER_TYPE}",
            "targetsInfo": [
                {
                    "prometheusHost": "${PROMETHEUS_HOST}",
                    "prometheusHostPort": ${PROMETHEUS_HOST_PORT},
                    "alertmanagerHost": "${ALERTMANAGER_HOST}",
                    "authInfo": {
                        "ssh_username": "${SSH_USERNAME}",
                        "ssh_password": "${SSH_PASSWORD}"
                    },
                    "alertRuleConfigPath": "${ALERT_RULE_CONFIG_PATH}",
                    "prometheusReloadApiEndpoint": "${PROMETHEUS_RELOAD_API}"
                }
            ]
        }
    }
}
EOF
)

# -------------------------------------------------------
# 5. Send PM Job Create Request
# -------------------------------------------------------
PM_JOB_URL="${PROTOCOL}://${HOST}:${PORT}/vnfpm/${API_VERSION}/pm_jobs"

echo ""
echo "📡 Sending PM Job Create request to: $PM_JOB_URL"
echo ""
echo "--- Request Payload ---"
echo "$PM_JOB_PAYLOAD"
echo "-----------------------"
echo ""

RESPONSE=$(curl -g -i -s -X POST "${PM_JOB_URL}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "User-Agent: ${USER_AGENT}" \
  -H "Version: ${API_VERSION_HEADER}" \
  -H "X-Auth-Token: ${TOKEN}" \
  -d "${PM_JOB_PAYLOAD}")

# -------------------------------------------------------
# 6. Parse and Display Response
# -------------------------------------------------------
HTTP_STATUS=$(echo "$RESPONSE" | awk '/^HTTP\// {print $2}' | tail -1)
BODY=$(echo "$RESPONSE" | awk '/^\r?$/{found=1; next} found{print}')

echo "--- Response (HTTP $HTTP_STATUS) ---"
echo "$BODY"
echo "------------------------------------"
echo ""

if [ "$HTTP_STATUS" == "201" ]; then
  echo "✅ PM Job created successfully (HTTP 201)."

  # Extract PM Job ID from response body
  PM_JOB_ID=$(echo "$BODY" | grep -o '"id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/"id"[[:space:]]*:[[:space:]]*"\([^"]*\)"/\1/')

  if [ -n "$PM_JOB_ID" ]; then
    echo ""
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│  PM Job ID: $PM_JOB_ID"
    echo "│"
    echo "│  ⚠  Save this PM Job ID — it is required for:"
    echo "│     - Fetching performance reports"
    echo "│     - Deleting or modifying this PM job"
    echo "│"
    echo "│  Report fetch URL:"
    echo "│  ${PROTOCOL}://${HOST}:${PORT}/vnfpm/${API_VERSION}/pm_jobs/${PM_JOB_ID}/reports/<reportId>"
    echo "└─────────────────────────────────────────────────────────┘"
  fi
else
  echo "❌ PM Job creation failed (HTTP $HTTP_STATUS)."
  echo "   Review the response body above for error details."
  exit 1
fi
