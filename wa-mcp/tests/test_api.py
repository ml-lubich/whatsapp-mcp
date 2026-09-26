"""Tests for wa_cli.api: httpx REST client for the Go bridge.

All HTTP is mocked with respx — these tests must never touch the real
bridge process, even though one may be live on localhost:8080 on the
machine running the suite.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from wa_cli import api

BASE_URL = "http://localhost:8080"

# Strings that stress the HTTP-boundary encoding path (json body round-trip).
EDGE_STRINGS = [
    pytest.param("", id="empty"),
    pytest.param("   ", id="whitespace-only"),
    pytest.param("x" * 100_000, id="very-long"),
    pytest.param("😀🎉👍🏽", id="emoji"),
    pytest.param("مرحبا بالعالم", id="rtl-arabic"),
    pytest.param("a​b​c", id="zero-width"),
    pytest.param('He said "hello"', id="embedded-quotes"),
    pytest.param("back\\slash\\path", id="backslash"),
    pytest.param("line1\nline2\r\nline3", id="newlines"),
    pytest.param("' OR 1=1--", id="sql-injection-shaped"),
    pytest.param('{"malicious": true}', id="json-breaking-braces"),
    pytest.param("\x00\x01\x1f control", id="control-chars"),
]

# Every distinct httpx exception type not already exercised by the pre-existing
# tests above (ConnectError, TimeoutException/ReadTimeout are covered there).
NEW_EXCEPTION_TYPES = [
    pytest.param(httpx.ConnectTimeout("timed out"), id="ConnectTimeout"),
    pytest.param(httpx.WriteTimeout("timed out"), id="WriteTimeout"),
    pytest.param(httpx.PoolTimeout("timed out"), id="PoolTimeout"),
    pytest.param(httpx.NetworkError("network error"), id="NetworkError"),
    pytest.param(httpx.ReadError("read error"), id="ReadError"),
    pytest.param(httpx.WriteError("write error"), id="WriteError"),
    pytest.param(httpx.CloseError("close error"), id="CloseError"),
    pytest.param(httpx.RemoteProtocolError("bad response"), id="RemoteProtocolError"),
]

STATUS_CODES = [200, 201, 204, 400, 401, 403, 404, 408, 429, 500, 502, 503, 504]


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


# --- download_media() ---


@respx.mock
def test_download_media_success():
    respx.post(f"{BASE_URL}/api/download").mock(
        return_value=httpx.Response(200, json={"success": True, "filename": "photo.jpg", "path": "/path/to/photo.jpg"})
    )

    ok, filename, path = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is True
    assert filename == "photo.jpg"
    assert path == "/path/to/photo.jpg"


@respx.mock
def test_download_media_failure_json():
    respx.post(f"{BASE_URL}/api/download").mock(
        return_value=httpx.Response(200, json={"success": False, "message": "media expired"})
    )

    ok, filename, detail = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is False
    assert detail == "media expired"


@respx.mock
def test_download_media_connect_error():
    respx.post(f"{BASE_URL}/api/download").mock(side_effect=httpx.ConnectError("refused"))

    ok, filename, detail = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is False
    assert detail == api.CONNECT_ERROR_DETAIL


@respx.mock
def test_download_media_timeout():
    respx.post(f"{BASE_URL}/api/download").mock(side_effect=httpx.TimeoutException("timed out"))

    ok, filename, detail = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is False
    assert detail == api.TIMEOUT_DETAIL


@respx.mock
def test_download_media_plain_text_error():
    respx.post(f"{BASE_URL}/api/download").mock(return_value=httpx.Response(500, text="internal server error"))

    ok, filename, detail = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is False
    assert detail == "internal server error"


# --- send_message() body-encoding round trips ---


@respx.mock
@pytest.mark.parametrize("message", EDGE_STRINGS)
def test_send_message_roundtrips_edge_message_bodies(message):
    route = respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json={"success": True}))

    api.send_message("14157863858@s.whatsapp.net", message, base_url=BASE_URL)

    sent = json.loads(route.calls.last.request.content)
    assert sent["message"] == message


@respx.mock
@pytest.mark.parametrize("recipient", EDGE_STRINGS)
def test_send_message_roundtrips_edge_recipient_bodies(recipient):
    route = respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json={"success": True}))

    api.send_message(recipient, "hello", base_url=BASE_URL)

    sent = json.loads(route.calls.last.request.content)
    assert sent["recipient"] == recipient


@respx.mock
@pytest.mark.parametrize(
    "jid",
    [
        pytest.param("14157863858@s.whatsapp.net", id="individual-jid"),
        pytest.param("120363319322076067@g.us", id="group-jid"),
        pytest.param("100455141081206@lid", id="lid-jid"),
        pytest.param("14157863858", id="bare-number-no-domain"),
        pytest.param("@s.whatsapp.net", id="empty-user-part"),
        pytest.param("a@b@c.whatsapp.net", id="multiple-at-signs"),
        pytest.param("14157863858@", id="trailing-at-no-domain"),
    ],
)
def test_send_message_roundtrips_jid_forms(jid):
    route = respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json={"success": True}))

    api.send_message(jid, "hello", base_url=BASE_URL)

    sent = json.loads(route.calls.last.request.content)
    assert sent["recipient"] == jid


@given(message=st.text(st.characters(blacklist_categories=("Cs",))))
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@respx.mock
def test_send_message_hypothesis_message_roundtrips_exactly(message):
    route = respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json={"success": True}))

    api.send_message("14157863858@s.whatsapp.net", message, base_url=BASE_URL)

    sent = json.loads(route.calls.last.request.content)
    assert sent["message"] == message


@given(recipient=st.text(st.characters(blacklist_categories=("Cs",))))
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@respx.mock
def test_send_message_hypothesis_recipient_roundtrips_exactly(recipient):
    route = respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json={"success": True}))

    api.send_message(recipient, "hello", base_url=BASE_URL)

    sent = json.loads(route.calls.last.request.content)
    assert sent["recipient"] == recipient


# --- send_message() response body edge cases ---


@respx.mock
def test_send_message_response_missing_success_key():
    respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json={"message": "no success field"}))

    ok, detail = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is False
    assert detail == "no success field"


@respx.mock
def test_send_message_response_success_as_truthy_string():
    respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json={"success": "true", "message": "ok"}))

    ok, detail = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is True


@respx.mock
def test_send_message_response_success_as_int_one():
    respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json={"success": 1}))

    ok, _ = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is True


@respx.mock
def test_send_message_response_success_as_int_zero():
    respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json={"success": 0}))

    ok, _ = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is False


@respx.mock
def test_send_message_response_bare_list_body_is_clean_failure():
    respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json=["unexpected", "list"]))

    ok, detail = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is False
    assert detail == "bridge returned HTTP 200"


@respx.mock
def test_send_message_response_bare_number_body_is_clean_failure():
    respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json=42))

    ok, detail = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is False
    assert detail == "bridge returned HTTP 200"


@respx.mock
def test_send_message_response_empty_json_object():
    respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json={}))

    ok, detail = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is False
    assert detail == ""


@respx.mock
def test_send_message_response_deeply_nested_body_does_not_crash():
    nested: dict = {"success": True, "message": "ok"}
    for _ in range(200):
        nested = {"wrap": nested}
    respx.post(f"{BASE_URL}/api/send").mock(
        return_value=httpx.Response(200, json={"success": True, "message": "ok", "extra": nested})
    )

    ok, detail = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is True
    assert detail == "ok"


@respx.mock
def test_send_message_response_missing_message_key_defaults_empty():
    respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json={"success": True}))

    ok, detail = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is True
    assert detail == ""


@respx.mock
def test_send_message_html_error_body_treated_as_plain_text():
    html = "<html><body><h1>502 Bad Gateway</h1></body></html>"
    respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(502, text=html))

    ok, detail = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is False
    assert detail == html


# --- send_message() every distinct httpx exception path ---


@respx.mock
@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(httpx.ConnectTimeout("timed out"), id="ConnectTimeout"),
        pytest.param(httpx.WriteTimeout("timed out"), id="WriteTimeout"),
        pytest.param(httpx.PoolTimeout("timed out"), id="PoolTimeout"),
    ],
)
def test_send_message_all_timeout_subtypes_map_to_timeout_detail(exc):
    respx.post(f"{BASE_URL}/api/send").mock(side_effect=exc)

    ok, detail = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is False
    assert detail == api.TIMEOUT_DETAIL


@respx.mock
@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(httpx.NetworkError("network error"), id="NetworkError"),
        pytest.param(httpx.ReadError("read error"), id="ReadError"),
        pytest.param(httpx.WriteError("write error"), id="WriteError"),
        pytest.param(httpx.CloseError("close error"), id="CloseError"),
        pytest.param(httpx.RemoteProtocolError("bad response"), id="RemoteProtocolError"),
    ],
)
def test_send_message_all_network_and_protocol_errors_map_to_connect_detail(exc):
    respx.post(f"{BASE_URL}/api/send").mock(side_effect=exc)

    ok, detail = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is False
    assert detail == api.CONNECT_ERROR_DETAIL


# --- send_message() status code matrix ---


@respx.mock
@pytest.mark.parametrize("status_code", STATUS_CODES)
def test_send_message_status_codes_with_json_body(status_code):
    success = status_code < 300
    respx.post(f"{BASE_URL}/api/send").mock(
        return_value=httpx.Response(status_code, json={"success": success, "message": f"status {status_code}"})
    )

    ok, detail = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is success
    assert detail == f"status {status_code}"


@respx.mock
@pytest.mark.parametrize("status_code", STATUS_CODES)
def test_send_message_status_codes_with_non_json_body(status_code):
    respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(status_code, text=f"plain error {status_code}"))

    ok, detail = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is False
    assert detail == f"plain error {status_code}"


@respx.mock
def test_send_message_status_code_with_empty_non_json_body_falls_back_to_status():
    respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(418, text=""))

    ok, detail = api.send_message("x", "hi", base_url=BASE_URL)

    assert ok is False
    assert detail == "bridge returned HTTP 418"


# --- download_media() edge-case ids and JSON bodies ---


@respx.mock
@pytest.mark.parametrize("message_id", EDGE_STRINGS)
def test_download_media_roundtrips_edge_message_ids(message_id):
    route = respx.post(f"{BASE_URL}/api/download").mock(
        return_value=httpx.Response(200, json={"success": True, "filename": "f", "path": "p"})
    )

    api.download_media(message_id, "chat456@s.whatsapp.net", base_url=BASE_URL)

    sent = json.loads(route.calls.last.request.content)
    assert sent["message_id"] == message_id


@respx.mock
@pytest.mark.parametrize("chat_jid", EDGE_STRINGS)
def test_download_media_roundtrips_edge_chat_jids(chat_jid):
    route = respx.post(f"{BASE_URL}/api/download").mock(
        return_value=httpx.Response(200, json={"success": True, "filename": "f", "path": "p"})
    )

    api.download_media("msg123", chat_jid, base_url=BASE_URL)

    sent = json.loads(route.calls.last.request.content)
    assert sent["chat_jid"] == chat_jid


@respx.mock
def test_download_media_success_missing_filename_key_defaults_empty():
    respx.post(f"{BASE_URL}/api/download").mock(return_value=httpx.Response(200, json={"success": True, "path": "/x"}))

    ok, filename, path = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is True
    assert filename == ""
    assert path == "/x"


@respx.mock
def test_download_media_success_missing_path_key_defaults_empty():
    respx.post(f"{BASE_URL}/api/download").mock(return_value=httpx.Response(200, json={"success": True, "filename": "f"}))

    ok, filename, path = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is True
    assert filename == "f"
    assert path == ""


@respx.mock
def test_download_media_path_traversal_filename_passed_through_unmodified():
    respx.post(f"{BASE_URL}/api/download").mock(
        return_value=httpx.Response(200, json={"success": True, "filename": "../../etc/passwd", "path": "/x"})
    )

    ok, filename, _ = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is True
    assert filename == "../../etc/passwd"


@respx.mock
def test_download_media_path_traversal_path_passed_through_unmodified():
    respx.post(f"{BASE_URL}/api/download").mock(
        return_value=httpx.Response(200, json={"success": True, "filename": "f", "path": "../../etc/passwd"})
    )

    ok, _, path = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is True
    assert path == "../../etc/passwd"


@respx.mock
def test_download_media_bare_list_body_is_clean_failure():
    respx.post(f"{BASE_URL}/api/download").mock(return_value=httpx.Response(200, json=["oops"]))

    ok, filename, detail = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is False
    assert filename == ""
    assert detail == "bridge returned HTTP 200"


@respx.mock
def test_download_media_html_error_body_treated_as_plain_text():
    html = "<html><body>404 Not Found</body></html>"
    respx.post(f"{BASE_URL}/api/download").mock(return_value=httpx.Response(404, text=html))

    ok, filename, detail = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is False
    assert detail == html


@respx.mock
def test_download_media_missing_message_key_on_failure_defaults_to_default_text():
    respx.post(f"{BASE_URL}/api/download").mock(return_value=httpx.Response(200, json={"success": False}))

    ok, filename, detail = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is False
    assert detail == "download failed"


@respx.mock
@pytest.mark.parametrize("status_code", STATUS_CODES)
def test_download_media_status_codes_with_json_body(status_code):
    success = status_code < 300
    respx.post(f"{BASE_URL}/api/download").mock(
        return_value=httpx.Response(
            status_code, json={"success": success, "filename": "f", "path": "p", "message": f"status {status_code}"}
        )
    )

    ok, filename, detail = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is success
    if success:
        assert filename == "f"
    else:
        assert detail == f"status {status_code}"


@respx.mock
@pytest.mark.parametrize("status_code", STATUS_CODES)
def test_download_media_status_codes_with_non_json_body(status_code):
    respx.post(f"{BASE_URL}/api/download").mock(
        return_value=httpx.Response(status_code, text=f"plain error {status_code}")
    )

    ok, filename, detail = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is False
    assert detail == f"plain error {status_code}"


@respx.mock
@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(httpx.ConnectTimeout("timed out"), id="ConnectTimeout"),
        pytest.param(httpx.WriteTimeout("timed out"), id="WriteTimeout"),
        pytest.param(httpx.PoolTimeout("timed out"), id="PoolTimeout"),
    ],
)
def test_download_media_all_timeout_subtypes_map_to_timeout_detail(exc):
    respx.post(f"{BASE_URL}/api/download").mock(side_effect=exc)

    ok, filename, detail = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is False
    assert detail == api.TIMEOUT_DETAIL


@respx.mock
@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(httpx.NetworkError("network error"), id="NetworkError"),
        pytest.param(httpx.ReadError("read error"), id="ReadError"),
        pytest.param(httpx.WriteError("write error"), id="WriteError"),
        pytest.param(httpx.CloseError("close error"), id="CloseError"),
        pytest.param(httpx.RemoteProtocolError("bad response"), id="RemoteProtocolError"),
    ],
)
def test_download_media_all_network_and_protocol_errors_map_to_connect_detail(exc):
    respx.post(f"{BASE_URL}/api/download").mock(side_effect=exc)

    ok, filename, detail = api.download_media("msg123", "chat456@s.whatsapp.net", base_url=BASE_URL)

    assert ok is False
    assert detail == api.CONNECT_ERROR_DETAIL


# --- bridge_alive() extra edge cases ---


@respx.mock
@pytest.mark.parametrize("timeout", [0, 0.001, 3600.0])
def test_bridge_alive_uses_the_given_timeout_value(timeout):
    route = respx.get(f"{BASE_URL}/").mock(return_value=httpx.Response(200))

    api.bridge_alive(BASE_URL, timeout=timeout)

    request = route.calls.last.request
    assert request.extensions["timeout"] == {
        "connect": timeout,
        "read": timeout,
        "write": timeout,
        "pool": timeout,
    }


@respx.mock
def test_bridge_alive_unicode_base_url_host_is_idna_encoded_and_reachable():
    unicode_base = "http://café.example.com:8080"
    respx.get("http://xn--caf-dma.example.com:8080/").mock(return_value=httpx.Response(200))

    assert api.bridge_alive(unicode_base) is True


@respx.mock
def test_bridge_alive_base_url_with_trailing_slash_does_not_double_slash():
    trailing = f"{BASE_URL}/"
    route = respx.get(f"{BASE_URL}/").mock(return_value=httpx.Response(200))

    assert api.bridge_alive(trailing) is True
    assert str(route.calls.last.request.url) == f"{BASE_URL}/"


@respx.mock
def test_send_message_base_url_with_trailing_slash_does_not_double_slash():
    route = respx.post(f"{BASE_URL}/api/send").mock(return_value=httpx.Response(200, json={"success": True}))

    api.send_message("x", "hi", base_url=f"{BASE_URL}/")

    assert str(route.calls.last.request.url) == f"{BASE_URL}/api/send"


@respx.mock
def test_download_media_base_url_with_trailing_slash_does_not_double_slash():
    route = respx.post(f"{BASE_URL}/api/download").mock(
        return_value=httpx.Response(200, json={"success": True, "filename": "f", "path": "p"})
    )

    api.download_media("msg", "chat", base_url=f"{BASE_URL}/")

    assert str(route.calls.last.request.url) == f"{BASE_URL}/api/download"


@respx.mock
@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(httpx.ConnectTimeout("timed out"), id="ConnectTimeout"),
        pytest.param(httpx.WriteTimeout("timed out"), id="WriteTimeout"),
        pytest.param(httpx.PoolTimeout("timed out"), id="PoolTimeout"),
        pytest.param(httpx.NetworkError("network error"), id="NetworkError"),
        pytest.param(httpx.ReadError("read error"), id="ReadError"),
        pytest.param(httpx.WriteError("write error"), id="WriteError"),
        pytest.param(httpx.CloseError("close error"), id="CloseError"),
        pytest.param(httpx.RemoteProtocolError("bad response"), id="RemoteProtocolError"),
    ],
)
def test_bridge_alive_every_exception_type_maps_to_false(exc):
    respx.get(f"{BASE_URL}/").mock(side_effect=exc)

    assert api.bridge_alive(BASE_URL) is False
