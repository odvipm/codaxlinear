import csv
import json
import os
from typing import Any

REPORT_HEADERS = [
    "coda_doc_id",
    "coda_page_id",
    "coda_page_name",
    "linear_project_id",
    "linear_document_id",
    "linear_document_url",
    "status",
    "images_migrated",
    "images_skipped",
    "error_message",
]

_EMPTY_STATE: dict = {"pages": {}, "uploaded_assets": {}}


def load_state(path: str) -> dict:
    """Load state.json; return empty state structure if file doesn't exist."""
    if not os.path.exists(path):
        return {"pages": {}, "uploaded_assets": {}}
    with open(path) as f:
        return json.load(f)


def save_state(path: str, state: dict) -> None:
    """Write state.json atomically (write to .tmp, rename)."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def append_report_row(path: str, row: dict[str, Any]) -> None:
    """Append one row to report.csv, writing header on first write."""
    write_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_HEADERS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow({h: row.get(h, "") for h in REPORT_HEADERS})
