import logging
import time

import httpx

LINEAR_GQL = "https://api.linear.app/graphql"
log = logging.getLogger(__name__)


class LinearClient:
    def __init__(self, api_token: str, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(
            headers={
                "Authorization": api_token,
                "Content-Type": "application/json",
            },
            timeout=60,
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    def _gql(self, query: str, variables: dict | None = None) -> dict:
        payload: dict = {"query": query}
        if variables:
            payload["variables"] = variables
        for attempt in range(5):
            r = self._http.post(LINEAR_GQL, json=payload)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                wait = 2 ** attempt
                log.warning("Linear transient error %s; backing off %ds", r.status_code, wait)
                time.sleep(wait)
                continue
            if r.status_code in (401, 403):
                raise PermissionError(
                    f"Linear auth error {r.status_code}: check LINEAR_API_TOKEN"
                )
            r.raise_for_status()
            data = r.json()
            if "errors" in data:
                raise RuntimeError(f"GraphQL errors: {data['errors']}")
            return data["data"]
        raise RuntimeError("Linear API max retries exceeded")

    # ── Public API ────────────────────────────────────────────────────────

    def get_viewer(self) -> dict:
        """Return the authenticated user: {id, name, email}."""
        data = self._gql("query { viewer { id name email } }")
        return data["viewer"]

    def get_projects(self) -> list[dict]:
        """Return all accessible projects: [{id, name}]."""
        nodes: list[dict] = []
        cursor: str | None = None
        while True:
            variables: dict = {"first": 250}
            if cursor:
                variables["after"] = cursor
            data = self._gql(
                """
                query Projects($first: Int!, $after: String) {
                  projects(first: $first, after: $after) {
                    nodes { id name }
                    pageInfo { hasNextPage endCursor }
                  }
                }
                """,
                variables,
            )
            page = data["projects"]
            nodes.extend(page["nodes"])
            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]
        return nodes

    def file_upload(self, content_type: str, filename: str, size: int) -> dict:
        """Request a presigned upload URL from Linear.

        Returns:
            {uploadUrl, assetUrl, headers: [{key, value}]}
        """
        query = """
        mutation FileUpload($contentType: String!, $filename: String!, $size: Int!) {
          fileUpload(contentType: $contentType, filename: $filename, size: $size) {
            success
            uploadFile {
              uploadUrl
              assetUrl
              headers { key value }
            }
          }
        }
        """
        data = self._gql(
            query,
            {"contentType": content_type, "filename": filename, "size": size},
        )
        result = data["fileUpload"]
        if not result.get("success"):
            raise RuntimeError(f"fileUpload mutation failed: {result}")
        return result["uploadFile"]

    def put_asset(self, upload_url: str, headers: list[dict], data: bytes) -> None:
        """PUT asset bytes to the presigned upload URL."""
        hdict = {h["key"]: h["value"] for h in headers}
        hdict["Content-Length"] = str(len(data))
        r = httpx.put(upload_url, content=data, headers=hdict, timeout=60)
        if r.status_code == 400 and "x-goog-content-length-range" in hdict:
            retry_headers = dict(hdict)
            retry_headers.pop("x-goog-content-length-range", None)
            retry_headers.pop("X-Goog-Content-Length-Range", None)
            r = httpx.put(upload_url, content=data, headers=retry_headers, timeout=60)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Linear asset upload failed {r.status_code}: {r.text[:500]}"
            ) from exc

    def document_create(
        self, title: str, content: str, project_id: str
    ) -> dict:
        """Create a project document. Returns {id, url}."""
        query = """
        mutation DocumentCreate(
          $title: String!
          $content: String!
          $projectId: String!
        ) {
          documentCreate(input: {
            title: $title
            content: $content
            projectId: $projectId
          }) {
            success
            document { id url }
          }
        }
        """
        data = self._gql(
            query,
            {"title": title, "content": content, "projectId": project_id},
        )
        result = data["documentCreate"]
        if not result.get("success"):
            raise RuntimeError(f"documentCreate mutation failed: {result}")
        return result["document"]

    def document_update(self, doc_id: str, content: str) -> dict:
        """Update a document's content. Returns {id, url}."""
        query = """
        mutation DocumentUpdate($id: String!, $content: String!) {
          documentUpdate(id: $id, input: { content: $content }) {
            success
            document { id url }
          }
        }
        """
        data = self._gql(query, {"id": doc_id, "content": content})
        result = data["documentUpdate"]
        if not result.get("success"):
            raise RuntimeError(f"documentUpdate mutation failed: {result}")
        return result["document"]

    def get_document(self, doc_id: str) -> dict | None:
        """Fetch a document by ID. Returns {id, content} or None if not found."""
        query = """
        query Document($id: String!) {
          document(id: $id) { id content }
        }
        """
        try:
            data = self._gql(query, {"id": doc_id})
            return data.get("document")
        except RuntimeError:
            return None
