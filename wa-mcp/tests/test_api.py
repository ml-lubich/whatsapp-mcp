"""Tests for wa_cli.api: httpx REST client for the Go bridge.

All HTTP is mocked with respx — these tests must never touch the real
bridge process, even though one may be live on localhost:8080 on the
machine running the suite.
"""

from __future__ import annotations

import httpx
import respx

from wa_cli import api

BASE_URL = "http://localhost:8080"


# --- send_message() ---


@respx.mock
def test_send_message_success_posts_exact_url_method_and_json():
    route = respx.post(f"{BASE_URL}/api/send").mock(
        return_value=httpx.Response(200, json={"success": True, "message": "sent"})
    )

    ok, detail = api.send_message("14157863858@s.whatsapp.net", "hello", base_url=BASE_URL)

    assert route.called
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url == f"{BASE_URL}/api/send"
    assert request.headers["content-type"] == "application/json"
    import json

    assert json.loads(request.content) == {
        "recipient": "14157863858@s.whatsapp.net",
        "message": "hello",
    }
    assert ok is True
    assert detail == "sent"


@respx.mock
def test_send_message_failure_http_500_with_success_false_body():
    respx.post(f"{BASE_URL}/api/send").mock(
        return_value=httpx.Response(500, json={"success": False, "message": "send failed: not connected"})
    )

    ok, detail = api.send_message("120363319322076067@g.us", "hi", base_url=BASE_URL)

    assert ok is False
    assert detail == "send failed: not connected"


@respx.mock
def test_send_message_success_false_with_200_status_is_treated_as_failure():
    respx.post(f"{BASE_URL}/api/send").mock(
        return_value=httpx.Response(200, json={"success": False, "message": "recipient required"})
    )

    ok, detail = api.send_message("", "hi", base_url=BASE_URL)

    assert ok is False
    assert detail == "recipient required"


@respx.mock
def test_send_message_connect_error_maps_to_actionable_message():
    respx.post(f"{BASE_URL}/api/send").mock(side_effect=httpx.ConnectError("connection refused"))

    ok, detail = api.send_message("100455141081206@lid", "hi", base_url=BASE_URL)

    assert ok is False
    assert detail == "bridge not running — try `wa up`"


# --- bridge_alive() ---


@respx.mock
def test_bridge_alive_true_on_mocked_404():
    respx.get(f"{BASE_URL}/").mock(return_value=httpx.Response(404))

    assert api.bridge_alive(BASE_URL) is True


@respx.mock
def test_bridge_alive_true_on_mocked_200():
    respx.get(f"{BASE_URL}/").mock(return_value=httpx.Response(200))

    assert api.bridge_alive(BASE_URL) is True


@respx.mock
def test_bridge_alive_false_on_connect_error():
    respx.get(f"{BASE_URL}/").mock(side_effect=httpx.ConnectError("connection refused"))

    assert api.bridge_alive(BASE_URL) is False


@respx.mock
def test_bridge_alive_false_on_timeout():
    respx.get(f"{BASE_URL}/").mock(side_effect=httpx.TimeoutException("timed out"))

    assert api.bridge_alive(BASE_URL) is False


@respx.mock
def test_bridge_alive_uses_short_default_timeout():
    respx.get(f"{BASE_URL}/").mock(return_value=httpx.Response(404))

    api.bridge_alive(BASE_URL)

    request = respx.calls.last.request
    assert request.method == "GET"
    assert request.url == f"{BASE_URL}/"


@respx.mock
def test_send_message_plain_text_error_body_is_clean_failure():
    respx.post(f"{BASE_URL}/api/send").mock(
        return_value=httpx.Response(400, text="Recipient is required\n")
    )

    ok, detail = api.send_message("", "hello", base_url=BASE_URL)

    assert ok is False
    assert detail == "Recipient is required"


@respx.mock
def test_send_message_empty_non_json_body_reports_http_status():
    respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(405, text=""))

    ok, detail = api.send_message("14157863858", "hello", base_url=BASE_URL)

    assert ok is False
    assert detail == "bridge returned HTTP 405"


@respx.mock
def test_send_message_timeout_maps_to_actionable_detail():
    respx.post(f"{BASE_URL}/api/send").mock(side_effect=httpx.ReadTimeout("timed out"))

    ok, detail = api.send_message("14157863858", "hello", base_url=BASE_URL)

    assert ok is False
    assert detail == api.TIMEOUT_DETAIL


@respx.mock
def test_send_message_sets_a_timeout_on_the_post():
    route = respx.post(f"{BASE_URL}/api/send").mock(
        return_value=httpx.Response(200, json={"success": True, "message": "sent"})
    )

    api.send_message("14157863858", "hello", base_url=BASE_URL)

    request = route.calls.last.request
    assert request.extensions["timeout"]["read"] == api.SEND_TIMEOUT
