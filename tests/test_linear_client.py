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
