import logging
import time
from urllib.parse import unquote, urlparse

import httpx

CODA_BASE = "https://coda.io/apis/v1"
log = logging.getLogger(__name__)


class CodaClient:
    def __init__(self, api_token: str, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(
            headers={"Authorization": f"Bearer {api_token}"},
            follow_redirects=True,
            timeout=30,
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{CODA_BASE}{path}"
        return self._get_url(url, params)

    def _get_url(self, url: str, params: dict | None = None) -> dict:
        while True:
            r = self._http.get(url, params=params)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", "5"))
                log.warning("Coda rate limit; sleeping %ds", retry_after)
                time.sleep(retry_after)
                continue
            if r.status_code in (401, 403):
                raise PermissionError(
                    f"Coda auth error {r.status_code}: check CODA_API_TOKEN"
                )
            r.raise_for_status()
            return r.json()

    def _post(self, path: str, json: dict | None = None) -> dict:
        url = f"{CODA_BASE}{path}"
        r = self._http.post(url, json=json)
        if r.status_code in (401, 403):
            raise PermissionError(
                f"Coda auth error {r.status_code}: check CODA_API_TOKEN"
            )
        r.raise_for_status()
        return r.json()

    def _paginate(self, path: str) -> list[dict]:
        """Fetch all pages of a list endpoint, returning combined items list."""
        items: list[dict] = []
        page_token: str | None = None
        while True:
            params: dict = {"pageToken": page_token} if page_token else {"limit": 50}
            data = self._get(path, params)
            items.extend(data.get("items", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return items

    # ── Public API ────────────────────────────────────────────────────────

    def list_docs(self) -> list[dict]:
        """Return list of accessible Coda docs: [{id, name, ...}]."""
        return self._paginate("/docs")

    def list_pages(self, doc_id: str) -> list[dict]:
        """Return all pages in a doc: [{id, name, parent, ...}]."""
        return self._paginate(f"/docs/{doc_id}/pages")

    def _export_page_content(self, doc_id: str, page_id: str, output_format: str) -> str:
        export = self._post(
            f"/docs/{doc_id}/pages/{page_id}/export",
            {"outputFormat": output_format},
        )
        status_url = export["href"]

        while True:
            try:
                status = self._get_url(status_url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    time.sleep(1)
                    continue
                raise
            if status.get("status") == "complete":
                download_link = status["downloadLink"]
                response = httpx.get(download_link, follow_redirects=True, timeout=60)
                response.raise_for_status()
                return response.text
            if status.get("status") in {"failed", "error"}:
                raise RuntimeError(f"Coda export failed for page {page_id}: {status}")
            time.sleep(1)

    def get_page_content_markdown(self, doc_id: str, page_id: str) -> str:
        """Export full Markdown content of a page."""
        return self._export_page_content(doc_id, page_id, "markdown")

    def get_page_content_html(self, doc_id: str, page_id: str) -> str:
        """Export full HTML content of a page."""
        return self._export_page_content(doc_id, page_id, "html")

    def download_asset(self, url: str) -> tuple[bytes, str, str]:
        """Download an asset URL.

        Returns:
            (bytes, content_type, filename)

        Raises:
            PermissionError: on 403 (signed URL likely expired — caller should
                             re-fetch the page Markdown and retry).
        """
        r = httpx.get(url, follow_redirects=True, timeout=60)
        if r.status_code == 403:
            raise PermissionError(
                f"Asset download 403 (signed URL may have expired): {url}"
            )
        r.raise_for_status()
        content_type = (
            r.headers.get("content-type", "application/octet-stream")
            .split(";")[0]
            .strip()
        )
        # extract filename from the final (post-redirect) URL path
        path = unquote(urlparse(str(r.url)).path)
        filename = path.split("/")[-1] or "asset"
        return r.content, content_type, filename
