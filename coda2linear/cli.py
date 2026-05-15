import argparse
import csv
import logging
import os
import sys
from urllib.parse import urlparse

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


def _migrate_one_page(
    entry: dict,
    state: dict,
    coda: CodaClient,
    linear: LinearClient,
    dry_run: bool,
) -> dict:
    """Migrate one Coda page to a Linear project document.

    Returns a report row dict.
    """
    page_id = entry["coda_page_id"]
    doc_id = entry["coda_doc_id"]
    project_id = entry["linear_project_id"]

    # fetch Markdown
    markdown = coda.get_page_content_markdown(doc_id, page_id)

    # extract all asset URLs
    urls = extract_asset_urls(markdown)

    url_map: dict[str, str] = {}
    callout_lines: list[str] = []
    images_migrated = 0
    images_skipped = 0

    for url in urls:
        if not should_rehost(url):
            continue

        # use cached result if available
        if url in state["uploaded_assets"]:
            url_map[url] = state["uploaded_assets"][url]
            images_migrated += 1
            continue

        # download asset (with one retry on signed-URL expiry)
        for attempt in range(2):
            try:
                asset_bytes, content_type, filename = coda.download_asset(url)
                break
            except PermissionError:
                if attempt == 0:
                    log.info("Signed URL expired; re-fetching page Markdown for %s", page_id)
                    markdown = coda.get_page_content_markdown(doc_id, page_id)
                    old_path = urlparse(url).path
                    fresh = next(
                        (u for u in extract_asset_urls(markdown) if urlparse(u).path == old_path),
                        None,
                    )
                    if fresh:
                        url = fresh
                else:
                    raise

        size = len(asset_bytes)
        is_oversized = size > MAX_ASSET_BYTES

        if is_oversized:
            callout_lines.append(oversized_asset_callout(url))
            images_skipped += 1
            continue

        # upload — external GIFs fall back to original URL on any error
        try:
            new_url, _ = _upload_asset(
                url, asset_bytes, content_type, filename, linear, state, dry_run
            )
        except Exception as upload_err:
            if is_external_gif_url(url, content_type):
                log.warning("External GIF rehost failed (%s); keeping original URL: %s", upload_err, url)
                callout_lines.append(external_gif_fallback_callout(url))
                images_skipped += 1
                continue
            raise  # Coda-hosted asset upload failure → abort page

        if new_url is None:
            callout_lines.append(oversized_asset_callout(url))
            images_skipped += 1
        elif new_url == _DRY_RUN_URL:
            images_migrated += 1
        else:
            url_map[url] = new_url
            images_migrated += 1

    # rewrite Markdown and append callouts
    final_markdown = rewrite_asset_urls(markdown, url_map)
    for callout in callout_lines:
        final_markdown += callout

    title = build_title(entry["coda_page_name"], entry.get("coda_parent_names", []))

    if dry_run:
        print(
            f"  [DRY RUN] '{title}' → project {project_id} "
            f"({images_migrated} assets, {images_skipped} skipped)"
        )
        return {
            "coda_doc_id": entry["coda_doc_id"],
            "coda_page_id": page_id,
            "coda_page_name": entry["coda_page_name"],
            "linear_project_id": project_id,
            "status": "dry_run",
            "images_migrated": images_migrated,
            "images_skipped": images_skipped,
        }

    doc = linear.document_create(title, final_markdown, project_id)
    print(f"  ✓ '{title}' → {doc['url']}")
    return {
        "coda_doc_id": entry["coda_doc_id"],
        "coda_page_id": page_id,
        "coda_page_name": entry["coda_page_name"],
        "linear_project_id": project_id,
        "linear_document_id": doc["id"],
        "linear_document_url": doc["url"],
        "status": "success",
        "images_migrated": images_migrated,
        "images_skipped": images_skipped,
    }


def cmd_migrate(args: argparse.Namespace) -> None:
    coda_token = os.environ.get("CODA_API_TOKEN")
    linear_token = os.environ.get("LINEAR_API_TOKEN")
    if not coda_token:
        sys.exit("Error: CODA_API_TOKEN not set")
    if not linear_token:
        sys.exit("Error: LINEAR_API_TOKEN not set")

    if not os.path.exists(args.mapping):
        sys.exit(f"Error: {args.mapping} not found — run 'coda2linear discover' first")

    with open(args.mapping, encoding="utf-8") as f:
        mapping_data = yaml.safe_load(f) or {}
    all_entries: list[dict] = mapping_data.get("pages", [])
    entries = [e for e in all_entries if e.get("linear_project_id")]

    if not entries:
        sys.exit(f"No entries with linear_project_id set in {args.mapping}. Nothing to do.")

    print(f"Found {len(entries)} page(s) to migrate (of {len(all_entries)} total in mapping).")
    if args.dry_run:
        print("DRY RUN — no writes to Linear.")

    with httpx.Client(
        headers={"Authorization": f"Bearer {coda_token}"},
        follow_redirects=True,
        timeout=60,
    ) as coda_http, httpx.Client(
        headers={
            "Authorization": linear_token,
            "Content-Type": "application/json",
        },
        timeout=60,
    ) as linear_http:
        coda = CodaClient(coda_token, coda_http)
        linear = LinearClient(linear_token, linear_http)

        _preflight(coda, linear, entries)

        state = load_state(args.state_file)
        succeeded = failed = skipped = 0

        for i, entry in enumerate(entries, 1):
            page_id = entry["coda_page_id"]
            page_status = state["pages"].get(page_id, {}).get("status", "")

            if page_status == "success":
                print(f"  [{i}/{len(entries)}] Skipping (already migrated): {entry['coda_page_name']}")
                skipped += 1
                continue

            print(f"  [{i}/{len(entries)}] Migrating: {entry['coda_page_name']}")
            try:
                result = _migrate_one_page(entry, state, coda, linear, args.dry_run)
                state["pages"][page_id] = {
                    "status": result["status"],
                    "linear_document_id": result.get("linear_document_id", ""),
                    "linear_document_url": result.get("linear_document_url", ""),
                }
                save_state(args.state_file, state)
                append_report_row(args.report, result)
                succeeded += 1
            except Exception as exc:
                reason = str(exc)
                log.error("Failed to migrate '%s': %s", entry["coda_page_name"], reason)
                state["pages"][page_id] = {"status": f"failed:{reason[:120]}"}
                save_state(args.state_file, state)
                append_report_row(args.report, {
                    **entry,
                    "status": "failed",
                    "error_message": reason[:200],
                })
                failed += 1

    print(f"\nDone. {succeeded} migrated, {skipped} skipped, {failed} failed.")
    if failed:
        print(f"Check {args.report} for details. Re-run migrate to retry failed pages.")
        sys.exit(1)


def cmd_verify(args: argparse.Namespace) -> None:
    linear_token = os.environ.get("LINEAR_API_TOKEN")
    if not linear_token:
        sys.exit("Error: LINEAR_API_TOKEN not set")

    if not os.path.exists(args.report):
        sys.exit(f"Error: {args.report} not found — run 'coda2linear migrate' first")

    with open(args.report, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r.get("status") == "success"]

    if not rows:
        print(f"No successfully migrated documents found in {args.report}. Nothing to verify.")
        return

    print(f"Verifying {len(rows)} document(s)...")

    with httpx.Client(
        headers={
            "Authorization": linear_token,
            "Content-Type": "application/json",
        },
        timeout=30,
    ) as linear_http:
        linear = LinearClient(linear_token, linear_http)
        failed: list[dict] = []

        for row in rows:
            doc = linear.get_document(row["linear_document_id"])
            if not doc or not (doc.get("content") or "").strip():
                failed.append(row)
                print(f"  ✗ MISSING/EMPTY: '{row['coda_page_name']}' (ID: {row['linear_document_id']})")
            else:
                print(f"  ✓ OK: '{row['coda_page_name']}'")

    if failed:
        print(f"\nVERIFY FAILED: {len(failed)}/{len(rows)} document(s) missing or empty.")
        sys.exit(1)
    else:
        print(f"\nVERIFY PASSED: all {len(rows)} document(s) confirmed.")


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
        cmd_migrate(args)
    elif args.command == "verify":
        cmd_verify(args)
