from flask import Flask, request, jsonify, make_response, Response
import requests
import logging
import os
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y", "on")


def env_json(name: str, default: Optional[Any] = None) -> Any:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError as exc:
        logging.error("Environment variable %s is not valid JSON: %s", name, exc)
        return default


# -----------------------------------------------------------------------------
# Existing Devtron trigger configuration - retained for /adapter/lccnsub
# -----------------------------------------------------------------------------
DEVTRON_URL = os.getenv("DEVTRON_URL", "").rstrip("/")
_RAW_REST_REQ_BODY = os.getenv("REST_REQ_BODY", "").strip()
try:
    REST_REQ_BODY = json.loads(_RAW_REST_REQ_BODY) if _RAW_REST_REQ_BODY else None
except json.JSONDecodeError:
    REST_REQ_BODY = None
    logging.warning("REST_REQ_BODY is not valid JSON; will be forwarded as raw text")

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "10"))
DEVTRON_VERIFY_SSL = env_bool("DEVTRON_VERIFY_SSL", "true")
DEVTRON_TOKEN = os.getenv("DEVTRON_TOKEN", "")

# -----------------------------------------------------------------------------
# PM job creation configuration
# -----------------------------------------------------------------------------
PM_JOB_CREATE_ON_STARTUP = env_bool("PM_JOB_CREATE_ON_STARTUP", "false")
PM_JOB_CREATE_STARTUP_DELAY_SECONDS = float(os.getenv("PM_JOB_CREATE_STARTUP_DELAY_SECONDS", "2"))
TACKER_PM_JOB_URL = os.getenv("TACKER_PM_JOB_URL", "").strip()
TACKER_PM_JOB_BODY = env_json("TACKER_PM_JOB_BODY", None)
TACKER_VERIFY_SSL = env_bool("TACKER_VERIFY_SSL", "true")
TACKER_TOKEN = os.getenv("TACKER_TOKEN", "").strip()
TACKER_USERNAME = os.getenv("TACKER_USERNAME", "").strip()
TACKER_PASSWORD = os.getenv("TACKER_PASSWORD", "").strip()
TACKER_REQUEST_TIMEOUT = float(os.getenv("TACKER_REQUEST_TIMEOUT", str(REQUEST_TIMEOUT)))

# Callback endpoint Basic Auth expected by Tacker validation/notification requests.
# Leave empty to disable callback auth validation.
PM_CALLBACK_USERNAME = os.getenv("PM_CALLBACK_USERNAME", "").strip()
PM_CALLBACK_PASSWORD = os.getenv("PM_CALLBACK_PASSWORD", "").strip()

# -----------------------------------------------------------------------------
# Pushgateway / Prometheus metric forwarding configuration
# -----------------------------------------------------------------------------
PUSHGATEWAY_BASE_URL = os.getenv("PUSHGATEWAY_BASE_URL", "http://localhost:9091").rstrip("/")
PUSHGATEWAY_VERIFY_SSL = env_bool("PUSHGATEWAY_VERIFY_SSL", "true")
PERFORMANCE_METRICS_MOCK_MODE = env_bool("PERFORMANCE_METRICS_MOCK_MODE", "false")

# -----------------------------------------------------------------------------
# Prometheus alert -> Devtron action API configuration
# -----------------------------------------------------------------------------
# Backward compatibility: existing healing configuration is retained.
DEVTRON_HEALING_URL = os.getenv("DEVTRON_HEALING_URL", DEVTRON_URL).rstrip("/")
DEVTRON_HEALING_BODY = env_json("DEVTRON_HEALING_BODY", REST_REQ_BODY)
DEVTRON_HEALING_VERIFY_SSL = env_bool("DEVTRON_HEALING_VERIFY_SSL", str(DEVTRON_VERIFY_SSL).lower())

# New generic action configuration. Add more actions in ConfigMap without code changes.
# Example:
# DEVTRON_ACTIONS={"heal":{"url":"http://...","body":{...}},"migrate":{"url":"http://...","body":{...}}}
DEVTRON_ACTIONS = env_json("DEVTRON_ACTIONS", {}) or {}
DEFAULT_DEVTRON_ACTION = os.getenv("DEFAULT_DEVTRON_ACTION", "heal").strip().lower() or "heal"

# Seed heal action from existing envs so older deployments continue to work.
if "heal" not in DEVTRON_ACTIONS:
    DEVTRON_ACTIONS["heal"] = {
        "url": DEVTRON_HEALING_URL,
        "body": DEVTRON_HEALING_BODY,
        "verify_ssl": DEVTRON_HEALING_VERIFY_SSL,
    }

# In-memory stores for demo/mock visibility only
MOCK_PUSHGATEWAY_STORE: List[Dict[str, Any]] = []
PM_PROCESSING_HISTORY: List[Dict[str, Any]] = []

# Create a Session with retry/backoff to handle transient network issues
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))
adapter = HTTPAdapter(max_retries=retries)
session.mount("http://", adapter)
session.mount("https://", adapter)


def filter_response_headers(headers):
    """Filter out hop-by-hop headers that should not be forwarded."""
    hop_by_hop = {
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade"
    }
    return {k: v for k, v in headers.items() if k.lower() not in hop_by_hop}


def build_tacker_headers(content_type: str = "application/json") -> Dict[str, str]:
    headers = {"Content-Type": content_type, "Accept": "application/json"}
    if TACKER_TOKEN:
        headers["Authorization"] = f"Bearer {TACKER_TOKEN}"
    return headers


def build_tacker_auth() -> Optional[HTTPBasicAuth]:
    if TACKER_USERNAME and TACKER_PASSWORD:
        return HTTPBasicAuth(TACKER_USERNAME, TACKER_PASSWORD)
    return None


def validate_callback_auth() -> Optional[Response]:
    """Validate Basic Auth for Tacker callback only when configured."""
    if not PM_CALLBACK_USERNAME and not PM_CALLBACK_PASSWORD:
        return None
    auth = request.authorization
    if not auth or auth.type.lower() != "basic":
        return jsonify({"error": "Missing Basic Auth credentials"}), 401
    if auth.username != PM_CALLBACK_USERNAME or auth.password != PM_CALLBACK_PASSWORD:
        return jsonify({"error": "Invalid Basic Auth credentials"}), 401
    return None


def create_pm_job_in_tacker() -> Tuple[bool, Dict[str, Any]]:
    """Create a PM job in Tacker using fully configurable URL and request body."""
    if not TACKER_PM_JOB_URL:
        return False, {"error": "TACKER_PM_JOB_URL not configured"}
    if not TACKER_PM_JOB_BODY:
        return False, {"error": "TACKER_PM_JOB_BODY not configured or invalid JSON"}

    try:
        logging.info("Creating PM job in Tacker: %s", TACKER_PM_JOB_URL)
        resp = session.post(
            TACKER_PM_JOB_URL,
            json=TACKER_PM_JOB_BODY,
            headers=build_tacker_headers(),
            timeout=TACKER_REQUEST_TIMEOUT,
            auth=build_tacker_auth(),
            verify=TACKER_VERIFY_SSL,
        )
        result = {
            "status_code": resp.status_code,
            "body": safe_json_or_text(resp),
        }
        if 200 <= resp.status_code < 300:
            logging.info("PM job created/accepted by Tacker: status=%s", resp.status_code)
            return True, result
        logging.error("Tacker PM job creation failed: status=%s body=%s", resp.status_code, resp.text)
        return False, result
    except requests.RequestException as exc:
        logging.exception("Failed to create PM job in Tacker")
        return False, {"error": "Failed to contact Tacker", "details": str(exc)}


def startup_pm_job_worker():
    time.sleep(PM_JOB_CREATE_STARTUP_DELAY_SECONDS)
    success, result = create_pm_job_in_tacker()
    if success:
        logging.info("Startup PM job creation completed successfully: %s", result)
    else:
        logging.error("Startup PM job creation failed but service will continue: %s", result)


def safe_json_or_text(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text


def sanitize_metric_name(metric_name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_:]", "_", metric_name or "generic_performance_metric")
    if not re.match(r"^[a-zA-Z_:]", name):
        name = f"metric_{name}"
    return name.lower()


def resolve_prometheus_metric(performance_metric: str) -> Tuple[str, str]:
    metric_lower = (performance_metric or "").lower()
    if "vcpu" in metric_lower or "cpu" in metric_lower:
        return "cpu_metrics", "cpu_utilisation"
    if "vdisk" in metric_lower or "disk" in metric_lower or "storage" in metric_lower or "filesystem" in metric_lower:
        return "disk_metrics", "disk_utilisation"
    if "vmemory" in metric_lower or "memory" in metric_lower or "mem" in metric_lower:
        return "memory_metrics", "memory_utilisation"
    if "network" in metric_lower or "interface" in metric_lower or "rx" in metric_lower or "tx" in metric_lower:
        return "network_metrics", "network_utilisation"
    return "generic_performance_metrics", sanitize_metric_name(performance_metric)


def label_value(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")


def build_prometheus_line(entry: Dict[str, Any], performance_value: Dict[str, Any]) -> Tuple[str, str]:
    object_type = entry.get("objectType", "unknown")
    instance_id = entry.get("objectInstanceId", "unknown")
    performance_metric = entry.get("performanceMetric", "")
    raw_value = performance_value.get("value")

    try:
        numeric_value = float(raw_value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid performance value: {raw_value}")

    job_name, prometheus_metric_name = resolve_prometheus_metric(performance_metric)
    line = (
        f'{prometheus_metric_name}'
        f'{{object_type="{label_value(object_type)}",'
        f'instance_id="{label_value(instance_id)}",'
        f'performance_metric="{label_value(performance_metric)}"}} {numeric_value}'
    )
    return job_name, line


def normalize_performance_entries(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return payload["entries"]
    if isinstance(payload, dict) and "performanceMetric" in payload:
        return [payload]
    raise ValueError("Expected payload with 'entries' list or a single performance report entry")


def push_performance_entries_to_pushgateway(payload: Any) -> List[Dict[str, Any]]:
    entries = normalize_performance_entries(payload)
    pushed_results = []

    for entry in entries:
        performance_values = entry.get("performanceValues") or []
        if not performance_values:
            raise ValueError("performanceValues is empty")

        for performance_value in performance_values:
            job_name, metric_line = build_prometheus_line(entry, performance_value)
            pushgateway_url = f"{PUSHGATEWAY_BASE_URL}/metrics/job/{job_name}"
            payload_text = metric_line + "\n"

            if PERFORMANCE_METRICS_MOCK_MODE:
                logging.info("Mock mode enabled. Skipping Pushgateway POST to %s", pushgateway_url)
                MOCK_PUSHGATEWAY_STORE.append({
                    "job": job_name,
                    "url": pushgateway_url,
                    "payload": payload_text,
                    "source": "mock-mode",
                })
                pushed_results.append({"job": job_name, "url": pushgateway_url, "mocked": True})
                continue

            logging.info("Pushing performance metric to Pushgateway: %s payload=%s", pushgateway_url, payload_text.strip())
            resp = session.post(
                pushgateway_url,
                data=payload_text,
                headers={"Content-Type": "text/plain; version=0.0.4"},
                timeout=REQUEST_TIMEOUT,
                verify=PUSHGATEWAY_VERIFY_SSL,
            )
            if not 200 <= resp.status_code < 300:
                raise requests.RequestException(
                    f"Pushgateway returned status={resp.status_code}, body={resp.text}"
                )
            pushed_results.append({"job": job_name, "url": pushgateway_url, "status_code": resp.status_code})

    return pushed_results


def fetch_pm_report_from_tacker(report_url: str) -> Any:
    if not report_url:
        raise ValueError("performanceReport.href is missing")
    report_url = report_url.replace(" ", "")
    logging.info("Fetching PM report from Tacker: %s", report_url)
    resp = session.get(
        report_url,
        headers=build_tacker_headers(),
        timeout=TACKER_REQUEST_TIMEOUT,
        auth=build_tacker_auth(),
        verify=TACKER_VERIFY_SSL,
    )
    if not 200 <= resp.status_code < 300:
        raise requests.RequestException(f"Tacker PM report fetch failed: status={resp.status_code}, body={resp.text}")
    return safe_json_or_text(resp)


def normalize_action_name(action_name: Optional[str]) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", (action_name or DEFAULT_DEVTRON_ACTION).strip().lower()) or DEFAULT_DEVTRON_ACTION


def resolve_alert_action(alert_payload: Any, requested_action: Optional[str] = None) -> str:
    """Resolve requested Devtron action without duplicating alert handling logic.

    Priority:
    1. Explicit URL path: /adapter/prometheus/alerts/<action>
    2. Query parameter: ?action=migrate
    3. Alert payload labels/annotations/commonLabels: action, remediation, workflow, operation
    4. DEFAULT_DEVTRON_ACTION, default heal
    """
    if requested_action:
        return normalize_action_name(requested_action)

    query_action = request.args.get("action")
    if query_action:
        return normalize_action_name(query_action)

    candidate_sections: List[Dict[str, Any]] = []
    if isinstance(alert_payload, dict):
        for key in ("labels", "annotations", "commonLabels", "commonAnnotations"):
            if isinstance(alert_payload.get(key), dict):
                candidate_sections.append(alert_payload[key])
        alerts = alert_payload.get("alerts")
        if isinstance(alerts, list) and alerts:
            first_alert = alerts[0] if isinstance(alerts[0], dict) else {}
            for key in ("labels", "annotations"):
                if isinstance(first_alert.get(key), dict):
                    candidate_sections.append(first_alert[key])

    for section in candidate_sections:
        for key in ("action", "remediation", "workflow", "operation", "devtron_action"):
            value = section.get(key)
            if value:
                return normalize_action_name(str(value))

    return normalize_action_name(DEFAULT_DEVTRON_ACTION)


def build_devtron_action_body(configured_body: Any, alert_payload: Any, action_name: str) -> Any:
    if isinstance(configured_body, dict):
        body = dict(configured_body)
        body.setdefault("source", "prometheus-alert")
        body.setdefault("action", action_name)
        body.setdefault("alertPayload", alert_payload)
        return body
    return configured_body


def call_devtron_action_api(action_name: str, alert_payload: Any) -> requests.Response:
    action_name = normalize_action_name(action_name)
    action_config = DEVTRON_ACTIONS.get(action_name)
    if not isinstance(action_config, dict):
        raise ValueError(f"Devtron action '{action_name}' is not configured")

    action_url = str(action_config.get("url", "")).rstrip("/")
    if not action_url:
        raise ValueError(f"Devtron action '{action_name}' URL is not configured")

    action_body = build_devtron_action_body(action_config.get("body"), alert_payload, action_name)
    verify_ssl = action_config.get("verify_ssl", DEVTRON_VERIFY_SSL)
    if isinstance(verify_ssl, str):
        verify_ssl = verify_ssl.strip().lower() in ("1", "true", "yes", "y", "on")

    headers = {"Content-Type": "application/json"}
    token = action_config.get("token", DEVTRON_TOKEN)
    if token:
        headers["token"] = str(token)

    logging.info("Calling Devtron action API: action=%s url=%s", action_name, action_url)
    return session.post(
        action_url,
        json=action_body,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
        verify=bool(verify_ssl),
    )


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"}), 200


@app.route("/adapter/lccnsub", methods=["GET", "POST"])
def lccnsub_handler():
    logging.info(f"Incoming {request.method} request at {request.path}")

    auth = None
    if request.authorization and request.authorization.type.lower() == "basic":
        auth = HTTPBasicAuth(request.authorization.username, request.authorization.password)
    else:
        logging.error("Missing Basic Auth credentials in request.")
        return jsonify({"error": "Missing or invalid Basic Auth credentials"}), 401

    if request.method == "GET":
        logging.info("Responding with 204 No Content for GET request.")
        return make_response("", 204)

    if not DEVTRON_URL:
        logging.error("DEVTRON_URL is not configured")
        return jsonify({"error": "DEVTRON_URL not configured"}), 500

    try:
        logging.info("----- Incoming POST Request Dump -----")
        logging.info(f"Headers: {dict(request.headers)}")
        try:
            logging.info(f"Received JSON body: {request.get_json(silent=True)}")
            logging.info(f"Sending JSON body: {REST_REQ_BODY}")
        except Exception:
            logging.warning("Failed to parse JSON body")
        logging.info(f"Raw body: {request.get_data(as_text=True)}")
        logging.info("----- End of Request Dump -----")

        content_type = request.headers.get("Content-Type", "")
        if content_type and "application/json" in content_type.lower():
            payload = REST_REQ_BODY
            is_json = True
        elif "application/x-www-form-urlencoded" in content_type:
            if not request.form:
                return jsonify({"error": "Empty form data"}), 400
            payload = REST_REQ_BODY
            is_json = True
        else:
            payload = request.get_data() or b""
            is_json = False

        logging.info(f"Forwarding POST to Devtron (json={is_json}, payload_present={bool(payload)})")

        forward_headers = {"token": DEVTRON_TOKEN}
        if "token" in request.headers:
            forward_headers["token"] = request.headers["token"]
        if "Authorization" in request.headers:
            forward_headers["Authorization"] = request.headers["Authorization"]
        if "X-Request-ID" in request.headers:
            forward_headers["X-Request-ID"] = request.headers["X-Request-ID"]
        if content_type:
            forward_headers["Content-Type"] = content_type

        if is_json:
            resp = session.post(
                DEVTRON_URL,
                json=payload,
                headers=forward_headers,
                timeout=REQUEST_TIMEOUT,
                auth=auth,
                verify=DEVTRON_VERIFY_SSL,
            )
        else:
            resp = session.post(
                DEVTRON_URL,
                data=payload,
                headers=forward_headers,
                timeout=REQUEST_TIMEOUT,
                auth=auth,
                verify=DEVTRON_VERIFY_SSL,
            )

        logging.info(f"Devtron responded: status={resp.status_code} content-length={len(resp.content)}")

        resp_headers = filter_response_headers(resp.headers)
        content_type_resp = resp_headers.get("Content-Type")

        if 200 <= resp.status_code < 300:
            logging.info(f"Devtron responded {resp.status_code}, returning 204 No Content")
            return "", 204

        response = Response(resp.content, status=resp.status_code, headers=resp_headers)
        if content_type_resp:
            response.mimetype = content_type_resp.split(";")[0].strip()
        return response

    except requests.Timeout:
        logging.exception("Timeout when contacting Devtron")
        return jsonify({"error": "Timeout contacting Devtron"}), 504
    except requests.RequestException as e:
        logging.exception(f"Error forwarding to Devtron: {e}")
        return jsonify({"error": "Failed to contact Devtron", "details": str(e)}), 502
    except Exception as e:
        logging.exception(f"Unhandled error: {e}")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@app.route("/adapter/pm/jobs", methods=["POST"])
def create_pm_job_api():
    """Manual trigger for PM job creation using TACKER_PM_JOB_URL and TACKER_PM_JOB_BODY."""
    success, result = create_pm_job_in_tacker()
    return jsonify(result), 201 if success else 500


@app.route("/adapter/performance-metrics", methods=["POST"])
def performance_metrics_handler():
    """Accept PM report payload directly and push metrics to Prometheus Pushgateway."""
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Invalid or missing JSON body"}), 400
    try:
        pushed_results = push_performance_entries_to_pushgateway(payload)
        logging.info("Performance metrics processed: %s", pushed_results)
        return "", 204
    except ValueError as exc:
        logging.exception("Invalid performance metrics payload")
        return jsonify({"error": str(exc)}), 400
    except requests.RequestException as exc:
        logging.exception("Failed to contact Pushgateway")
        return jsonify({"error": "Failed to contact Pushgateway", "details": str(exc)}), 502
    except Exception as exc:
        logging.exception("Unhandled error while processing performance metrics")
        return jsonify({"error": "Internal server error", "details": str(exc)}), 500


@app.route("/api/pm/callback", methods=["GET", "POST"])
def pm_callback_handler():
    """Tacker PM callback endpoint.

    GET  -> callback validation, returns 204.
    POST -> extracts performanceReport.href, fetches report from Tacker, pushes metrics to Pushgateway.
    """
    auth_error = validate_callback_auth()
    if auth_error:
        return auth_error

    if request.method == "GET":
        logging.info("Tacker callback validation received; returning 204")
        return "", 204

    notification = request.get_json(silent=True)
    if notification is None:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    try:
        report_url = (
            notification.get("_links", {})
            .get("performanceReport", {})
            .get("href", "")
        )
        report_payload = fetch_pm_report_from_tacker(report_url)
        pushed_results = push_performance_entries_to_pushgateway(report_payload)
        PM_PROCESSING_HISTORY.append({
            "notification": notification,
            "report_url": report_url,
            "report_payload": report_payload,
            "pushed_results": pushed_results,
        })
        logging.info("PM callback processed successfully for pmJobId=%s", notification.get("pmJobId"))
        return "", 204
    except ValueError as exc:
        logging.exception("Invalid PM callback notification")
        return jsonify({"error": str(exc)}), 400
    except requests.RequestException as exc:
        logging.exception("Failed while processing PM callback")
        return jsonify({"error": "Failed while processing PM callback", "details": str(exc)}), 502
    except Exception as exc:
        logging.exception("Unhandled PM callback error")
        return jsonify({"error": "Internal server error", "details": str(exc)}), 500


@app.route("/adapter/prometheus/alerts", methods=["POST"])
@app.route("/adapter/prometheus/alerts/<action_name>", methods=["POST"])
@app.route("/api/prometheus/alerts", methods=["POST"])
@app.route("/api/prometheus/alerts/<action_name>", methods=["POST"])
def prometheus_alert_handler(action_name: Optional[str] = None):
    """Webhook endpoint for Prometheus/Alertmanager to trigger Devtron actions.

    Existing behavior remains: /adapter/prometheus/alerts defaults to heal.
    New behavior: use /adapter/prometheus/alerts/migrate, ?action=migrate, or alert labels to trigger migrate.
    """
    alert_payload = request.get_json(silent=True)
    if alert_payload is None:
        return jsonify({"error": "Invalid or missing JSON body"}), 400
    try:
        resolved_action = resolve_alert_action(alert_payload, action_name)
        resp = call_devtron_action_api(resolved_action, alert_payload)
        if 200 <= resp.status_code < 300:
            logging.info("Devtron action API accepted alert; action=%s returning 204", resolved_action)
            return "", 204
        return Response(resp.content, status=resp.status_code, headers=filter_response_headers(resp.headers))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 500
    except requests.RequestException as exc:
        logging.exception("Failed to contact Devtron action API")
        return jsonify({"error": "Failed to contact Devtron action API", "details": str(exc)}), 502
    except Exception as exc:
        logging.exception("Unhandled Prometheus alert processing error")
        return jsonify({"error": "Internal server error", "details": str(exc)}), 500


@app.route("/metrics/job/<job_name>", methods=["POST"])
def mock_pushgateway(job_name: str):
    """Local mock Pushgateway endpoint for demo/testing.

    Configure PUSHGATEWAY_BASE_URL=http://<adapter-host>:6000 to use this endpoint.
    """
    payload = request.get_data(as_text=True)
    MOCK_PUSHGATEWAY_STORE.append({
        "job": job_name,
        "url": request.url,
        "payload": payload,
        "source": "mock-pushgateway-endpoint",
    })
    logging.info("Mock Pushgateway accepted job=%s payload=%s", job_name, payload.strip())
    return "", 202


@app.route("/mock-pushgateway/store", methods=["GET", "DELETE"])
def mock_pushgateway_store():
    if request.method == "DELETE":
        MOCK_PUSHGATEWAY_STORE.clear()
        return "", 204
    return jsonify({"items": MOCK_PUSHGATEWAY_STORE, "count": len(MOCK_PUSHGATEWAY_STORE)}), 200


@app.route("/adapter/pm/history", methods=["GET", "DELETE"])
def pm_processing_history():
    if request.method == "DELETE":
        PM_PROCESSING_HISTORY.clear()
        return "", 204
    return jsonify({"items": PM_PROCESSING_HISTORY, "count": len(PM_PROCESSING_HISTORY)}), 200


if PM_JOB_CREATE_ON_STARTUP:
    threading.Thread(target=startup_pm_job_worker, daemon=True).start()


if __name__ == "__main__":
    # For production, run under a WSGI server (gunicorn) instead of Flask builtin server
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "6000")))
