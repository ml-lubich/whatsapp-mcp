"""httpx REST client for the Go bridge (send + liveness probe).

The bridge exposes only POST /api/send and POST /api/download — there is
no health endpoint. Liveness is therefore a cheap GET to "/": any HTTP
response (including 404) means the server is up; only a connection
error or timeout means it is down.
"""

from __future__ import annotations

import httpx

CONNECT_ERROR_DETAIL = "bridge not running — try `wa up`"


def send_message(recipient: str, message: str, *, base_url: str) -> tuple[bool, str]:
    """POST {base_url}/api/send with {"recipient", "message"}.

    Returns (ok, detail) where detail is the bridge's "message" field, or
    an actionable message if the bridge is unreachable.
    """
    try:
        response = httpx.post(
            f"{base_url}/api/send",
            json={"recipient": recipient, "message": message},
        )
    except httpx.ConnectError:
        return False, CONNECT_ERROR_DETAIL

    body = response.json()
    return bool(body.get("success")), body.get("message", "")


def bridge_alive(base_url: str, timeout: float = 1.0) -> bool:
    """Check bridge liveness via a cheap GET to "/".

    Any HTTP response (including 404 — the bridge has no route at "/")
    means the server is up. Only a connection error or timeout means down.
    """
    try:
        httpx.get(f"{base_url}/", timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException):
        return False
    return True
