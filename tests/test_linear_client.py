import httpx

from coda2linear.linear_client import LinearClient


class RecordingHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None):
        self.calls.append({"url": url, "json": json})
        request = httpx.Request("POST", url, json=json)
        status_code, payload = self.responses.pop(0)
        return httpx.Response(status_code, json=payload, request=request)


def test_linear_retries_transient_server_errors(monkeypatch):
    monkeypatch.setattr("coda2linear.linear_client.time.sleep", lambda _seconds: None)
    http = RecordingHttp(
        [
            (502, {"message": "bad gateway"}),
            (
                200,
                {
                    "data": {
                        "documentCreate": {
                            "success": True,
                            "document": {"id": "doc-1", "url": "https://linear.app/doc-1"},
                        }
                    }
                },
            ),
        ]
    )
    linear = LinearClient("token", http)

    assert linear.document_create("Title", "Content", "project-1") == {
        "id": "doc-1",
        "url": "https://linear.app/doc-1",
    }
    assert len(http.calls) == 2


def test_put_asset_uses_exact_presigned_headers(monkeypatch):
    put_calls = []

    def fake_put(url, **kwargs):
        put_calls.append({"url": url, **kwargs})
        request = httpx.Request("PUT", url)
        return httpx.Response(200, request=request)

    monkeypatch.setattr("coda2linear.linear_client.httpx.put", fake_put)
    linear = LinearClient("token")

    linear.put_asset(
        "https://uploads.linear.app/file",
        [
            {"key": "Content-Type", "value": "image/png"},
            {"key": "x-goog-content-length-range", "value": "0,100"},
        ],
        b"image-bytes",
    )

    assert put_calls == [
        {
            "url": "https://uploads.linear.app/file",
            "content": b"image-bytes",
            "headers": {
                "Content-Type": "image/png",
                "x-goog-content-length-range": "0,100",
                "Content-Length": "11",
            },
            "timeout": 60,
        }
    ]


def test_put_asset_adds_content_type_when_signed_url_requires_it(monkeypatch):
    put_calls = []

    def fake_put(url, **kwargs):
        put_calls.append({"url": url, **kwargs})
        request = httpx.Request("PUT", url)
        return httpx.Response(200, request=request)

    monkeypatch.setattr("coda2linear.linear_client.httpx.put", fake_put)
    linear = LinearClient("token")

    linear.put_asset(
        "https://uploads.linear.app/file",
        [{"key": "x-goog-content-length-range", "value": "0,100"}],
        b"image-bytes",
        "image/gif",
    )

    assert put_calls[0]["headers"]["content-type"] == "image/gif"


def test_document_update_sends_content_update():
    http = RecordingHttp(
        [
            (
                200,
                {
                    "data": {
                        "documentUpdate": {
                            "success": True,
                            "document": {"id": "doc-1", "url": "https://linear.app/doc-1"},
                        }
                    }
                },
            ),
        ]
    )
    linear = LinearClient("token", http)

    assert linear.document_update("doc-1", "New content") == {
        "id": "doc-1",
        "url": "https://linear.app/doc-1",
    }
    payload = http.calls[0]["json"]
    assert "documentUpdate" in payload["query"]
    assert payload["variables"] == {"id": "doc-1", "content": "New content"}


def test_put_asset_retries_without_content_length_range_on_400(monkeypatch):
    put_calls = []

    def fake_put(url, **kwargs):
        put_calls.append({"url": url, **kwargs})
        request = httpx.Request("PUT", url)
        if len(put_calls) == 1:
            return httpx.Response(400, text="bad request", request=request)
        return httpx.Response(200, request=request)

    monkeypatch.setattr("coda2linear.linear_client.httpx.put", fake_put)
    linear = LinearClient("token")

    linear.put_asset(
        "https://uploads.linear.app/file",
        [
            {"key": "Content-Type", "value": "image/png"},
            {"key": "x-goog-content-length-range", "value": "0,100"},
        ],
        b"image-bytes",
    )

    assert "x-goog-content-length-range" in put_calls[0]["headers"]
    assert "x-goog-content-length-range" not in put_calls[1]["headers"]
