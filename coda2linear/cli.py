import argparse
import logging
import os
import sys

import httpx
import yaml

from .coda_client import CodaClient
from .linear_client import LinearClient
from .state import load_state, save_state, append_report_row
from .transform import (
    build_title,
    count_table_dimensions,
    extract_asset_urls,
    external_gif_fallback_callout,
    is_external_gif_url,
    is_gif,
    oversized_asset_callout,
    rewrite_asset_urls,
    should_rehost,
)

log = logging.getLogger(__name__)

MAX_ASSET_BYTES = 25 * 1024 * 1024   # 25 MB — Linear hard cap
LARGE_GIF_BYTES = 10 * 1024 * 1024   # 10 MB — log conversion suggestion
_DRY_RUN_URL = "__dry_run_placeholder__"


# ── discover ──────────────────────────────────────────────────────────────────

def cmd_discover(args: argparse.Namespace) -> None:
    token = os.environ.get("CODA_API_TOKEN")
    if not token:
        sys.exit("Error: CODA_API_TOKEN environment variable not set")

    with httpx.Client(
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=True,
        timeout=30,
    ) as http:
        coda = CodaClient(token, http)
        print("Fetching docs from Coda...")
        docs = coda.list_docs()
        print(f"Found {len(docs)} docs. Fetching pages...")

        entries: list[dict] = []
        for doc in docs:
            pages = coda.list_pages(doc["id"])
            # build id→page map for parent chain resolution
            page_map = {p["id"]: p for p in pages}

            for page in pages:
                parent_names: list[str] = []
                # Coda page objects have a 'parent' dict with an 'id' field
                parent_ref = page.get("parent") or {}
                pid: str | None = parent_ref.get("id") if isinstance(parent_ref, dict) else None
                while pid and pid in page_map:
                    parent_names.insert(0, page_map[pid]["name"])
                    next_ref = page_map[pid].get("parent") or {}
                    pid = next_ref.get("id") if isinstance(next_ref, dict) else None

                entries.append({
                    "coda_doc_id": doc["id"],
                    "coda_doc_name": doc["name"],
                    "coda_page_id": page["id"],
                    "coda_page_name": page["name"],
                    "coda_parent_names": parent_names,
                    "linear_project_id": "",  # user fills this in
                })

    mapping_path = getattr(args, "output", "mapping.yaml")
    with open(mapping_path, "w", encoding="utf-8") as f:
        yaml.dump({"pages": entries}, f, default_flow_style=False, allow_unicode=True)

    print(f"\nWrote {mapping_path} with {len(entries)} pages across {len(docs)} docs.")
    print("Next steps:")
    print("  1. Open mapping.yaml")
    print("  2. Fill in 'linear_project_id' for each page you want to migrate")
    print("  3. Run: coda2linear migrate")


# ── migrate helpers ───────────────────────────────────────────────────────────

def _preflight(coda: CodaClient, linear: LinearClient, entries: list[dict]) -> None:
    """Verify API tokens and all linear_project_id values before touching Linear."""
    print("Running pre-flight checks...")

    viewer = linear.get_viewer()
    print(f"  Linear ✓  authenticated as {viewer['name']} ({viewer['email']})")

    docs = coda.list_docs()
    print(f"  Coda   ✓  authenticated, {len(docs)} doc(s) accessible")

    projects = {p["id"]: p["name"] for p in linear.get_projects()}
    mapped_ids = {e["linear_project_id"] for e in entries if e.get("linear_project_id")}
    missing = [pid for pid in mapped_ids if pid not in projects]
    if missing:
        for pid in missing:
            pages_for_pid = [e["coda_page_name"] for e in entries if e.get("linear_project_id") == pid]
            print(
                f"  ERROR: linear_project_id '{pid}' not found in Linear "
                f"(affects: {', '.join(pages_for_pid)})",
                file=sys.stderr,
            )
        sys.exit("Pre-flight failed. Fix mapping.yaml and retry.")

    print(f"  Projects ✓  {len(mapped_ids)} unique project ID(s) all verified.")


def _upload_asset(
    url: str,
    asset_bytes: bytes,
    content_type: str,
    filename: str,
    linear: LinearClient,
    state: dict,
    dry_run: bool,
) -> tuple[str | None, bool]:
    """Upload one asset to Linear (or simulate in dry-run).

    Returns:
        (new_url, rehosted): new_url is None if the asset was skipped/oversized.
    """
    size = len(asset_bytes)

    if is_gif(url, content_type) and size > LARGE_GIF_BYTES:
        log.warning(
            "GIF %.1f MB > 10 MB — consider converting to MP4: %s",
            size / 1024 / 1024,
            url,
        )

    if size > MAX_ASSET_BYTES:
        log.warning("Asset %.1f MB exceeds 25 MB Linear cap; skipping: %s", size / 1024 / 1024, url)
        return None, False

    if dry_run:
        return _DRY_RUN_URL, True

    upload_info = linear.file_upload(content_type, filename, size)
    linear.put_asset(upload_info["uploadUrl"], upload_info["headers"], asset_bytes)
    new_url = upload_info["assetUrl"]
    state["uploaded_assets"][url] = new_url
    return new_url, True


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="coda2linear",
        description="Migrate Coda documentation to Linear project documents",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # discover
    disc = sub.add_parser("discover", help="List Coda pages and write mapping.yaml")
    disc.add_argument("--output", default="mapping.yaml", help="Output mapping file (default: mapping.yaml)")

    # migrate (full definition added in Task 10)
    migrate_p = sub.add_parser("migrate", help="Migrate pages to Linear")
    migrate_p.add_argument("--dry-run", action="store_true", help="Simulate without writing to Linear")
    migrate_p.add_argument("--mapping", default="mapping.yaml")
    migrate_p.add_argument("--state-file", default="state.json")
    migrate_p.add_argument("--report", default="report.csv")

    # verify (full definition added in Task 12)
    verify_p = sub.add_parser("verify", help="Confirm migrated documents exist in Linear")
    verify_p.add_argument("--report", default="report.csv")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.command == "discover":
        cmd_discover(args)
    elif args.command == "migrate":
        print("Error: migrate not yet implemented", file=sys.stderr)
        sys.exit(1)
    elif args.command == "verify":
        print("Error: verify not yet implemented", file=sys.stderr)
        sys.exit(1)
