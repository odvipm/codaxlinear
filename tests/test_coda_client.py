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
        if isinstance(payload, str):
            return httpx.Response(200, text=payload, request=request)
        return httpx.Response(200, json=payload, request=request)

    def post(self, url, json=None):
        self.calls.append({"method": "POST", "url": url, "json": json})
        request = httpx.Request("POST", url, json=json)
        payload = self.post_responses.pop(0)
        return httpx.Response(202, json=payload, request=request)


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


def test_get_page_content_markdown_exports_and_downloads_markdown():
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
            "# Exported Page\n\nBody text.",
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
    assert http.calls[2] == {
        "method": "GET",
        "url": "https://exports.coda.io/page-1.md",
        "params": None,
    }
