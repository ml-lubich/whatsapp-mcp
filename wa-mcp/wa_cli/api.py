"""httpx REST client for the Go bridge (send + liveness probe).

The bridge exposes only POST /api/send and POST /api/download — there is
no health endpoint. Liveness is therefore a cheap GET to "/": any HTTP
response (including 404) means the server is up; only a connection
error or timeout means it is down.
"""

from __future__ import annotations

import httpx

CONNECT_ERROR_DETAIL = "bridge not running — try `wa up`"
TIMEOUT_DETAIL = "bridge did not respond in time — check `wa logs`"
SEND_TIMEOUT = 30.0


def send_message(recipient: str, message: str, *, base_url: str) -> tuple[bool, str]:
    """POST {base_url}/api/send with {"recipient", "message"}.

    Returns (ok, detail) where detail is the bridge's "message" field, or
    an actionable message if the bridge is unreachable.
    """
    base_url = base_url.rstrip("/")
    try:
        response = httpx.post(
            f"{base_url}/api/send",
            json={"recipient": recipient, "message": message},
            timeout=SEND_TIMEOUT,
        )
    except (httpx.NetworkError, httpx.ProtocolError):
        return False, CONNECT_ERROR_DETAIL
    except httpx.TimeoutException:
        return False, TIMEOUT_DETAIL

    try:
        body = response.json()
    except ValueError:
        # The bridge rejects bad requests with plain-text http.Error bodies.
        return False, response.text.strip() or f"bridge returned HTTP {response.status_code}"
    if not isinstance(body, dict):
        return False, f"bridge returned HTTP {response.status_code}"
    return bool(body.get("success")), body.get("message", "")


def download_media(message_id: str, chat_jid: str, *, base_url: str) -> tuple[bool, str, str]:
    """POST {base_url}/api/download with {"message_id", "chat_jid"}.

    Returns (ok, filename, path_or_detail).
    """
    base_url = base_url.rstrip("/")
    try:
        response = httpx.post(
            f"{base_url}/api/download",
            json={"message_id": message_id, "chat_jid": chat_jid},
            timeout=SEND_TIMEOUT,
        )
    except (httpx.NetworkError, httpx.ProtocolError):
        return False, "", CONNECT_ERROR_DETAIL
    except httpx.TimeoutException:
        return False, "", TIMEOUT_DETAIL

    try:
        body = response.json()
    except ValueError:
        return False, "", response.text.strip() or f"bridge returned HTTP {response.status_code}"
    if not isinstance(body, dict):
        return False, "", f"bridge returned HTTP {response.status_code}"

    ok = bool(body.get("success"))
    if ok:
        return True, body.get("filename", ""), body.get("path", "")
    return False, body.get("filename", ""), body.get("message", "download failed")


def bridge_alive(base_url: str, timeout: float = 1.0) -> bool:
    """Check bridge liveness via a cheap GET to "/".

    Any HTTP response (including 404 — the bridge has no route at "/")
    means the server is up. Only a connection error or timeout means down.
    """
    base_url = base_url.rstrip("/")
    try:
        httpx.get(f"{base_url}/", timeout=timeout)
    except (httpx.NetworkError, httpx.ProtocolError, httpx.TimeoutException):
        return False
    return True
