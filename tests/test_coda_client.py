import httpx

from coda2linear.coda_client import CodaClient


class RecordingHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None):
        self.calls.append({"url": url, "params": params})
        request = httpx.Request("GET", url, params=params)
        payload = self.responses.pop(0)
        return httpx.Response(200, json=payload, request=request)


def test_paginate_uses_only_page_token_after_first_page():
    http = RecordingHttp(
        [
            {"items": [{"id": "page-1"}], "nextPageToken": "next-token"},
            {"items": [{"id": "page-2"}]},
        ]
    )
    client = CodaClient("token", http)

    assert client.list_pages("doc-1") == [{"id": "page-1"}, {"id": "page-2"}]
    assert http.calls[0]["params"] == {"limit": 50}
    assert http.calls[1]["params"] == {"pageToken": "next-token"}
