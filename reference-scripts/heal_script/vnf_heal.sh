#!/bin/bash
# vnf_heal.sh — Perform VNF Heal using config file only (no user input)

CONFIG_FILE="./vnf_config.conf"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "Config file not found: $CONFIG_FILE"
  exit 1
fi

# Load config
while IFS='=' read -r key value; do
  [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
  key=$(echo "$key" | xargs)
  value=$(echo "$value" | xargs)
  declare "$key=$value"
done < "$CONFIG_FILE"

# === Build JSON Payload from Config ===
OPTIONAL_FIELDS=""

if [ -n "$CAUSE" ]; then
  OPTIONAL_FIELDS+="\"cause\": \"${CAUSE}\""
fi

if [ -n "$ALL_PARAM" ]; then
  [[ -n "$OPTIONAL_FIELDS" ]] && OPTIONAL_FIELDS+=", "
  OPTIONAL_FIELDS+="\"additionalParams\": {\"all\": ${ALL_PARAM}}"
fi

if [ -n "$VNFC_INPUT" ]; then
  [[ -n "$OPTIONAL_FIELDS" ]] && OPTIONAL_FIELDS+=", "
  IFS=',' read -ra VNFC_ARRAY <<< "$VNFC_INPUT"
  VNFC_JSON="["
  for id in "${VNFC_ARRAY[@]}"; do
    VNFC_JSON+="\"$(echo "$id" | xargs)\","
  done
  VNFC_JSON="${VNFC_JSON%,}]"
  OPTIONAL_FIELDS+="\"vnfcInstanceId\": $VNFC_JSON"
fi

if [ -z "$OPTIONAL_FIELDS" ]; then
  HEAL_PAYLOAD="{}"
else
  HEAL_PAYLOAD="{${OPTIONAL_FIELDS}}"
fi

# === Keystone Auth Token Request ===
AUTH_PAYLOAD=$(cat <<EOF
{
  "auth": {
    "identity": {
      "methods": ["password"],
      "password": {
        "user": {
          "name": "$USERNAME",
          "domain": { "id": "$USER_DOMAIN_ID" },
          "password": "$PASSWORD"
        }
      }
    },
    "scope": {
      "project": {
        "name": "$PROJECT_NAME",
        "domain": { "id": "$PROJECT_DOMAIN_ID" }
      }
    }
  }
}
EOF
)

TOKEN=$(curl -s -i -X POST "$KEYSTONE_URL" \
  -H "Content-Type: application/json" \
  --data "$AUTH_PAYLOAD" | awk '/^X-Subject-Token:/ {print $2}' | tr -d '\r')

if [ -z "$TOKEN" ]; then
  echo "Failed to retrieve Keystone token."
  exit 1
else
  echo "Keystone token retrieved."
fi

# === Heal Request ===
HEAL_URL="$PROTOCOL://$HOST:$PORT/vnflcm/$API_VERSION/vnf_instances/$VNF_INSTANCE_ID/heal"
echo -e "\n📡 Sending Heal request to: $HEAL_URL"
echo "Payload: $HEAL_PAYLOAD"

curl -g -i -X POST "$HEAL_URL" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "User-Agent: $USER_AGENT" \
  -H "Version: $API_VERSION_HEADER" \
  -H "X-Auth-Token: $TOKEN" \
  -d "$HEAL_PAYLOAD"
