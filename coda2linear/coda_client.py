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

    def _paginate(self, path: str) -> list[dict]:
        """Fetch all pages of a list endpoint, returning combined items list."""
        items: list[dict] = []
        page_token: str | None = None
        while True:
            params: dict = {"limit": 50}
            if page_token:
                params["pageToken"] = page_token
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

    def get_page_content_markdown(self, doc_id: str, page_id: str) -> str:
        """Fetch full Markdown content of a page (handles pagination)."""
        chunks: list[str] = []
        page_token: str | None = None
        while True:
            params: dict = {"format": "markdown", "limit": 50}
            if page_token:
                params["pageToken"] = page_token
            data = self._get(f"/docs/{doc_id}/pages/{page_id}/content", params)
            for item in data.get("items", []):
                if isinstance(item, dict) and "text" in item:
                    chunks.append(item["text"])
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return "\n".join(chunks)

    def download_asset(self, url: str) -> tuple[bytes, str, str]:
        """Download an asset URL.

        Returns:
            (bytes, content_type, filename)

        Raises:
            PermissionError: on 403 (signed URL likely expired — caller should
                             re-fetch the page Markdown and retry).
        """
        r = self._http.get(url)
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
