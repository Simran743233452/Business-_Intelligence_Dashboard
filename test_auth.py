"""Local test for bearer-token authentication on the streamable-http MCP
endpoint - no Claude Pro or Render required.

Run with:
    python test_auth.py

Starts mcp_server.py as a subprocess in streamable-http mode with a
throwaway MCP_AUTH_TOKEN, then verifies:
  - GET /health works with no Authorization header
  - POST /mcp is rejected (401) with no Authorization header
  - POST /mcp is rejected (401) with an incorrect token
  - POST /mcp succeeds with the correct token

Uses only the standard library (urllib) so it doesn't add a test-only
dependency. Does not touch Google Sheets.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

TEST_TOKEN = "test-local-auth-token-12345"
PORT = "8799"  # unlikely to collide with a real dev server
BASE_URL = f"http://127.0.0.1:{PORT}"

INITIALIZE_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test-auth-client", "version": "0.1"},
    },
}


def _request(path: str, method: str = "GET", token: str | None = None, body: dict | None = None):
    """Make a request and return (status_code, body_bytes) - HTTPError
    responses (like our 401s) are captured rather than raised."""
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json, text/event-stream"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _wait_for_server():
    for _ in range(40):
        try:
            status, _ = _request("/health")
            if status == 200:
                return
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.5)
    raise RuntimeError("Server did not start in time")


def main():
    env = dict(os.environ)
    env["MCP_TRANSPORT"] = "streamable-http"
    env["MCP_PORT"] = PORT
    env.pop("PORT", None)
    env["MCP_AUTH_TOKEN"] = TEST_TOKEN

    proc = subprocess.Popen(
        [sys.executable, "mcp_server.py"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_server()

        print("=== GET /health with no Authorization header ===")
        status, body = _request("/health")
        print(status, body)
        assert status == 200, f"Expected /health to work unauthenticated, got {status}"
        print("OK: /health is reachable without a token.\n")

        print("=== POST /mcp with no Authorization header ===")
        status, body = _request("/mcp", method="POST", body=INITIALIZE_PAYLOAD)
        print(status, body)
        assert status == 401, f"Expected 401 with no token, got {status}: {body}"
        print("OK: request without a token is rejected.\n")

        print("=== POST /mcp with an incorrect token ===")
        status, body = _request("/mcp", method="POST", token="the-wrong-token", body=INITIALIZE_PAYLOAD)
        print(status, body)
        assert status == 401, f"Expected 401 with an incorrect token, got {status}: {body}"
        print("OK: request with an incorrect token is rejected.\n")

        print("=== POST /mcp with the correct token ===")
        status, body = _request("/mcp", method="POST", token=TEST_TOKEN, body=INITIALIZE_PAYLOAD)
        print(status, body)
        assert status == 200, f"Expected 200 with the correct token, got {status}: {body}"
        assert b'"protocolVersion"' in body, "Expected a valid MCP initialize response"
        print("OK: request with the correct token succeeds.\n")

        print("All authentication tests passed.")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
