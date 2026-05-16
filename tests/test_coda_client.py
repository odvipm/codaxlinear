import httpx
from coda2linear.coda_client import CodaClient


class RecordingHttp:
    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.calls = []

    def get(self, url, params=None):
        self.calls.append({"method": "GET", "url": url, "params": params})
        request = httpx.Request("GET", url, params=params)
        payload = self.get_responses.pop(0)
        if isinstance(payload, tuple):
            status_code, body = payload
            return httpx.Response(status_code, json=body, request=request)
        if isinstance(payload, str):
            return httpx.Response(200, text=payload, request=request)
        return httpx.Response(200, json=payload, request=request)

    def post(self, url, json=None):
        self.calls.append({"method": "POST", "url": url, "json": json})
        request = httpx.Request("POST", url, json=json)
        payload = self.post_responses.pop(0)
        return httpx.Response(202, json=payload, request=request)


def stub_export_download(monkeypatch, text: str = "# Exported Page\n"):
    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(200, text=text, request=request)

    monkeypatch.setattr("coda2linear.coda_client.httpx.get", fake_get)


def test_paginate_uses_only_page_token_after_first_page():
    http = RecordingHttp(
        get_responses=[
            {"items": [{"id": "page-1"}], "nextPageToken": "next-token"},
            {"items": [{"id": "page-2"}]},
        ]
    )
    client = CodaClient("token", http)

    assert client.list_pages("doc-1") == [{"id": "page-1"}, {"id": "page-2"}]
    assert http.calls[0]["params"] == {"limit": 50}
    assert http.calls[1]["params"] == {"pageToken": "next-token"}


def test_get_page_content_markdown_exports_and_downloads_markdown(monkeypatch):
    stub_export_download(monkeypatch, "# Exported Page\n\nBody text.")
    http = RecordingHttp(
        post_responses=[
            {
                "id": "export-1",
                "status": "inProgress",
                "href": "https://coda.io/apis/v1/docs/doc-1/pages/page-1/export/export-1",
            }
        ],
        get_responses=[
            {
                "id": "export-1",
                "status": "complete",
                "downloadLink": "https://exports.coda.io/page-1.md",
            },
        ],
    )
    client = CodaClient("token", http)

    assert client.get_page_content_markdown("doc-1", "page-1") == "# Exported Page\n\nBody text."
    assert http.calls[0] == {
        "method": "POST",
        "url": "https://coda.io/apis/v1/docs/doc-1/pages/page-1/export",
        "json": {"outputFormat": "markdown"},
    }
    assert http.calls[1] == {
        "method": "GET",
        "url": "https://coda.io/apis/v1/docs/doc-1/pages/page-1/export/export-1",
        "params": None,
    }


def test_get_page_content_html_exports_html(monkeypatch):
    stub_export_download(monkeypatch, "<p>Body</p><img src='https://example.com/a.png'>")
    http = RecordingHttp(
        post_responses=[
            {
                "id": "export-1",
                "status": "inProgress",
                "href": "https://coda.io/apis/v1/docs/doc-1/pages/page-1/export/export-1",
            }
        ],
        get_responses=[
            {
                "id": "export-1",
                "status": "complete",
                "downloadLink": "https://exports.coda.io/page-1.html",
            },
        ],
    )
    client = CodaClient("token", http)

    assert client.get_page_content_html("doc-1", "page-1") == (
        "<p>Body</p><img src='https://example.com/a.png'>"
    )
    assert http.calls[0] == {
        "method": "POST",
        "url": "https://coda.io/apis/v1/docs/doc-1/pages/page-1/export",
        "json": {"outputFormat": "html"},
    }


def test_get_page_content_markdown_retries_export_status_404(monkeypatch):
    monkeypatch.setattr("coda2linear.coda_client.time.sleep", lambda _seconds: None)
    stub_export_download(monkeypatch, "# Exported Page\n")
    http = RecordingHttp(
        post_responses=[
            {
                "id": "export-1",
                "status": "inProgress",
                "href": "https://coda.io/apis/v1/docs/doc-1/pages/page-1/export/export-1",
            }
        ],
        get_responses=[
            (404, {"message": "not ready"}),
            {
                "id": "export-1",
                "status": "complete",
                "downloadLink": "https://exports.coda.io/page-1.md",
            },
        ],
    )
    client = CodaClient("token", http)

    assert client.get_page_content_markdown("doc-1", "page-1") == "# Exported Page\n"
    assert [call["url"] for call in http.calls].count(
        "https://coda.io/apis/v1/docs/doc-1/pages/page-1/export/export-1"
    ) == 2


def test_get_page_content_markdown_downloads_export_without_coda_auth(monkeypatch):
    http = RecordingHttp(
        post_responses=[
            {
                "id": "export-1",
                "status": "inProgress",
                "href": "https://coda.io/apis/v1/docs/doc-1/pages/page-1/export/export-1",
            }
        ],
        get_responses=[
            {
                "id": "export-1",
                "status": "complete",
                "downloadLink": "https://exports.coda.io/page-1.md",
            },
        ],
    )
    download_calls = []

    def fake_get(url, **kwargs):
        download_calls.append({"url": url, **kwargs})
        request = httpx.Request("GET", url)
        return httpx.Response(200, text="# Exported Page\n", request=request)

    monkeypatch.setattr("coda2linear.coda_client.httpx.get", fake_get)
    client = CodaClient("token", http)

    assert client.get_page_content_markdown("doc-1", "page-1") == "# Exported Page\n"
    assert http.calls[-1]["url"] != "https://exports.coda.io/page-1.md"
    assert download_calls == [
        {
            "url": "https://exports.coda.io/page-1.md",
            "follow_redirects": True,
            "timeout": 60,
        }
    ]


def test_download_asset_uses_unauthenticated_request(monkeypatch):
    download_calls = []

    def fake_get(url, **kwargs):
        download_calls.append({"url": url, **kwargs})
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            content=b"image-bytes",
            headers={"content-type": "image/png"},
            request=request,
        )

    monkeypatch.setattr("coda2linear.coda_client.httpx.get", fake_get)
    client = CodaClient("token", RecordingHttp())

    assert client.download_asset("https://exports.coda.io/images/pouch.png") == (
        b"image-bytes",
        "image/png",
        "pouch.png",
    )
    assert download_calls == [
        {
            "url": "https://exports.coda.io/images/pouch.png",
            "follow_redirects": True,
            "timeout": 60,
        }
    ]
