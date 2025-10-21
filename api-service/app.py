from flask import Flask, request, jsonify, make_response, Response
import requests
import logging
import os
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Configurable endpoints & credentials
DEVTRON_URL = os.getenv("DEVTRON_URL", "").rstrip("/")  # require explicit value in env ideally
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "10"))  # seconds
DEVTRON_VERIFY_SSL = os.getenv("DEVTRON_VERIFY_SSL", "true").lower() in ("1", "true", "yes")

# Token to include in Devtron requests
DEVTRON_TOKEN = os.getenv("DEVTRON_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJlbWFpbCI6IkFQSS1UT0tFTjpkb2NvbW8tYWRhcHRvci10ZXN0IiwidmVyc2lvbiI6IjEiLCJpc3MiOiJhcGlUb2tlbklzc3VlciIsImV4cCI6MTc2MzYzNTcwM30.4NcRkvHNCLvyvTeGT5wFCO9yCx5cHiF5vL4FofUbrBs")

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


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"}), 200


@app.route("/adapter/lccnsub", methods=["GET", "POST"])
def lccnsub_handler():
    logging.info(f"Incoming {request.method} request at {request.path}")

    auth = None
    if request.authorization and request.authorization.type.lower() == "basic":
        #logging.info("Using Basic Auth credentials supplied by client (Authorization header).")
        auth = HTTPBasicAuth(request.authorization.username, request.authorization.password)
    else:
        logging.error("Missing Basic Auth credentials in request.")
        return jsonify({"error": "Missing or invalid Basic Auth credentials"}), 401


    if request.method == "GET":
        logging.info("Responding with 204 No Content for GET request.")
        return make_response("", 204)

    # POST path
    if not DEVTRON_URL:
        logging.error("DEVTRON_URL is not configured")
        return jsonify({"error": "DEVTRON_URL not configured"}), 500

    try:

        # Dump incoming POST request
        logging.info("----- Incoming POST Request Dump -----")
        logging.info(f"Headers: {dict(request.headers)}")
        try:
            logging.info(f"JSON body: {request.get_json(silent=True)}")
        except Exception:
            logging.warning("Failed to parse JSON body")
        logging.info(f"Raw body: {request.get_data(as_text=True)}")
        logging.info("----- End of Request Dump -----")

        content_type = request.headers.get("Content-Type", "")
        # Determine payload type
        if content_type and "application/json" in content_type.lower():
            payload = request.get_json(silent=True)
            is_json = True
        else:
            payload = request.get_data() or b""
            is_json = False

        logging.info(f"Forwarding POST to Devtron (json={is_json}, payload_present={bool(payload)})")

        # Build headers to forward (selective)
        forward_headers = {}
        forward_headers["token"] = DEVTRON_TOKEN
       
        if "token" in request.headers:
            forward_headers["token"] = request.headers["token"]

 
        # Forward Authorization header if present (caller responsibility)
        if "Authorization" in request.headers:
            forward_headers["Authorization"] = request.headers["Authorization"]
        # Common tracing header(s)
        if "X-Request-ID" in request.headers:
            forward_headers["X-Request-ID"] = request.headers["X-Request-ID"]
        # Content-Type
        if content_type:
            forward_headers["Content-Type"] = content_type

        # Setup auth if provided
        #auth = None
        #if DEVTRON_USER and DEVTRON_PASSWORD:
        #    auth = HTTPBasicAuth(DEVTRON_USER, DEVTRON_PASSWORD)

        # Use session to forward
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

        # Prepare response to caller
        resp_headers = filter_response_headers(resp.headers)
        content_type_resp = resp_headers.get("Content-Type")

        response = Response(resp.content, status=resp.status_code, headers=resp_headers)
        if content_type_resp:
            # set mimetype without params
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


if __name__ == "__main__":
    # For production, run under a WSGI server (gunicorn) instead of Flask builtin server
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "6000")))
