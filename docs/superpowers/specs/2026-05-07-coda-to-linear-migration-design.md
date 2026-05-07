# Coda → Linear Documentation Migration — Design

**Date:** 2026-05-07
**Status:** Approved (pending implementation plan)

## Goal

Migrate 20–100 documentation pages from Coda into Linear as **project documents**, preserving Markdown content and re-hosting all images, attachments, and GIFs (including third-party embeds from Giphy/Tenor) into Linear-hosted assets.

## Non-goals

- No ongoing sync between Coda and Linear. This is a one-time migration.
- No preservation of Coda-specific elements that have no Linear equivalent: tables-as-databases, buttons, packs, formula columns, embedded views, automations. Where these appear, the migration leaves a marked placeholder.
- No nested document hierarchy in Linear. Coda sub-pages flatten to sibling Linear documents within the same project, with a parent-name prefix in the title.

## Scope

- **Source:** Coda workspace, ~20–100 pages organized in folders/sub-pages.
- **Target:** Linear project documents. Routing decision (which Coda page → which Linear project) is made per-page via a mapping file the user fills in once.
- **Content types:** prose, headings, lists, images, attachments, GIFs (including external embeds).

## Approach

A single Python 3 CLI tool, `coda2linear`, with three subcommands:

1. **`discover`** — list every Coda doc and page reachable with the user's API token. Emit `mapping.yaml` with one entry per page (Coda IDs and names pre-filled, `linear_project_id` blank for the user to fill).
2. **`migrate`** — process only mapping rows where `linear_project_id` is set. For each: fetch page Markdown from Coda, rewrite image/asset URLs by re-uploading to Linear, then `documentCreate` against the chosen project. Idempotent.
3. **`verify`** — confirm each migrated document still exists in Linear with non-empty content. Catches partial failures.

State files at project root:

- `mapping.yaml` — user input (page → project).
- `state.json` — migration state, keyed by Coda page ID. Tracks page status and an `uploaded_assets` cache (`coda_url → linear_asset_url`) so re-runs skip done work and don't re-upload duplicate images.
- `report.csv` — human-readable result log.

## Architecture

Modules:

- `coda_client.py` — thin wrapper over the Coda REST endpoints used: list docs, list pages, get page content as Markdown, download attachments. Handles pagination via `nextPageToken`.
- `linear_client.py` — thin wrapper over Linear's GraphQL: `viewer`, `projects`, `documentCreate`, `fileUpload`. Posts to `https://api.linear.app/graphql`.
- `transform.py` — pure functions for Markdown rewriting: image URL extraction, GIF detection, table fallback rendering, unsupported-block markers.
- `cli.py` — argparse entry point with three subcommands.

External dependencies: `httpx` (HTTP), `pyyaml` (mapping file), `pytest` (tests). Standard library otherwise.

## Data flow (per page)

```
Coda page (doc_id, page_id)
  ↓
GET /docs/{doc_id}/pages/{page_id}/content?format=markdown   (paginated; concatenate)
  ↓
Scan Markdown body for asset references:
  • ![alt](https://codahosted.io/...)        inline image
  • ![alt][ref] + [ref]: https://...         reference-style image
  • <img src="...">                          raw HTML img
  • [filename](https://codahosted.io/...)    file attachment
  • External GIF URLs (giphy.com, tenor.com, media.giphy.com, imgur, etc.)
  ↓
For each asset URL:
  1. GET asset bytes (follow redirects; signed Coda URLs return 302).
  2. Linear: mutation fileUpload(contentType, filename, size)
     → returns { uploadUrl, assetUrl, headers[] }.
  3. PUT bytes to uploadUrl with returned headers.
  4. Record (asset_url_old → assetUrl_new) in state.json `uploaded_assets`.
  ↓
Rewrite Markdown body: replace each old URL with new Linear assetUrl.
  ↓
Insert placeholder callouts for unsupported Coda elements:
  • Tables → render as Markdown table if simple (≤ 6 columns, ≤ 50 rows);
    otherwise: "> ⚠ Coda table omitted: <name>" with link back to Coda.
  • Buttons / packs / formulas → "> ⚠ Coda <element> not migrated".
  ↓
Linear: mutation documentCreate(input: {
  title:     <hierarchy-prefixed page name>,
  content:   <rewritten markdown>,
  projectId: <from mapping.yaml>
})
  → returns { document.id, document.url }
  ↓
Append row to report.csv; write success entry to state.json.
```

## Page hierarchy

Linear documents do not nest. Each Coda page — parent or sub-page — becomes one Linear document, and each appears as its own row in `mapping.yaml`. Default behavior: a sub-page inherits its parent's `linear_project_id` when the user fills in the parent's row, so a whole Coda page tree lands as siblings in one Linear project. The user can override per-row if a sub-page belongs to a different project. To preserve visual hierarchy in the flattened layout, page titles are prefixed with the parent chain: `"Onboarding / Day 1 / Setup"`.

## Asset handling

### Linear's upload sequence

```
1. mutation FileUpload {
     fileUpload(contentType: "image/png", filename: "screenshot.png", size: 48213) {
       success
       uploadFile {
         uploadUrl                    presigned PUT URL
         assetUrl                     URL to embed in Markdown
         headers { key value }        headers required on PUT
       }
     }
   }
2. PUT <uploadUrl> with returned headers and raw bytes.
3. assetUrl is what gets written into the rewritten Markdown.
```

### Rules in `transform.py`

- **Content-Type:** sniff from the Coda response's `Content-Type`; fall back to extension; default `application/octet-stream` for unknown.
- **Filename:** preserve original filename when present in the Coda URL; otherwise generate `<coda_page_id>-<sha1[:8]>.<ext>`.
- **Size limit:** Linear caps uploads at ~25 MB. Oversized → log a warning, leave the original Coda URL in the Markdown, insert `> ⚠ Asset exceeds Linear size limit; original Coda link retained` callout.
- **Dedup:** `uploaded_assets` cache in `state.json` is keyed by source URL. The same image referenced in 5 pages uploads once.

### GIF handling

**GIFs hosted on Coda** (`codahosted.io/.../*.gif`): same as images, with `image/gif` content type passed to `fileUpload` and `.gif` extension preserved (Linear's renderer dispatches on both). Stricter pre-check: GIFs > 10 MB log a conversion-to-MP4 suggestion but still attempt upload (Linear renders both inline up to its size cap).

**Embedded GIFs from third parties** (Giphy, Tenor, Imgur, etc., introduced via Coda's `/giphy` slash command): default behavior is **rehost** — download from the third party, upload to Linear, rewrite URL. This makes the GIF survive third-party deletion. Fallback: if rehost fails (403, geo-block, host rejects bot UA), leave the original URL — Linear renders external images inline. The report flags any docs that ended up depending on external hosts.

A `is_gif(url, content_type)` helper checks both URL extension and `Content-Type`, because Giphy URLs commonly include `?cid=...` querystrings and some hosts mislabel content type vs. extension.

### Non-image attachments

Coda surfaces file attachments as `[filename](url)` link syntax rather than `![]()` image syntax. Same flow: download, upload via `fileUpload` with the actual content type (`application/pdf`, etc.), rewrite the link.

## Error handling

### Per-page failures

Coda fetch failure, image upload failure, or `documentCreate` error → mark page `failed:<reason>` in `state.json`, append to `report.csv`, continue to next page. **No partial Linear documents are created**: if any image upload fails after retries, the page aborts before `documentCreate` is called.

### Cross-run failures

- **Coda rate limit (429):** respect `Retry-After` header; sleep then retry.
- **Linear rate limit (complexity-based):** exponential backoff up to 5 retries.
- **Auth (401/403) on either side:** hard-fail the run with a clear message. Tokens are wrong; continuing wastes API budget.

### Interruption recovery

`state.json` is flushed after each page completes. Re-running `migrate` skips pages with status `success` and retries `failed` pages. The `uploaded_assets` cache means already-uploaded images are not re-uploaded on re-run.

### Coda signed-URL expiry

If an image GET returns 403 because the signed URL expired between page fetch and download, re-fetch the page Markdown (Coda regenerates signed URLs on each export call) and retry once.

## Operability

- **Dry-run:** `coda2linear migrate --dry-run` performs every step except `documentCreate` and the image PUT. Logs what *would* happen, including image counts and rewritten Markdown size. Sanity-check before any Linear writes.
- **Pre-flight check** at the start of `migrate`: verifies both API tokens work (`viewer` on Linear, list-docs on Coda) and that every `linear_project_id` in the mapping resolves to an existing project the user can write to. Fails fast on config errors.
- **Logging:** structured logs to stderr (`INFO` per page, `WARNING` per fallback, `ERROR` per failure). One progress line per page to stdout.
- **Report CSV columns:** `coda_doc_id, coda_page_id, coda_page_name, linear_project_id, linear_document_id, linear_document_url, status, images_migrated, images_skipped, error_message`.

## Testing

- **Unit tests** (`pytest`) on `transform.py` — pure functions covering image URL extraction (inline, reference, raw HTML), GIF detection across URL/content-type combos, table-fallback rendering, unsupported-block markers.
- **Integration test** against one disposable Coda doc and one disposable Linear project. End-to-end run, assertions on the resulting Linear document's content. Set up manually once, re-runnable.
- **Pre-flight check** built into `migrate` (see Operability) — catches config errors before any writes.

No HTTP mocking. For a one-shot migration tool, mocks add maintenance burden without catching the bugs that actually fire (real API quirks, real image edge cases).

## Out of scope (explicit)

- Continuous sync between Coda and Linear.
- Migration of Coda Pack data, button automations, formula columns, or interactive controls.
- Conversion of Coda databases (tables-as-data) into Linear data structures — they are flattened to Markdown tables or placeholder callouts only.
- Rendering Coda-hosted embedded views (Calendar, Kanban, etc.).
- Linear permission management or user mapping. Documents are created under whatever user owns the API token.

## Configuration

Environment variables consumed by the CLI:

- `CODA_API_TOKEN` — Coda personal API token.
- `LINEAR_API_TOKEN` — Linear personal API key.

Both required for `migrate` and `verify`. `discover` only requires `CODA_API_TOKEN`.

## Success criteria

1. Every page in `mapping.yaml` with a `linear_project_id` set ends up as a Linear project document.
2. All Coda-hosted images, attachments, and GIFs are re-hosted on Linear's storage; no Markdown link still points to `codahosted.io`.
3. Third-party-embedded GIFs are either rehosted or, if rehost fails, flagged in `report.csv`.
4. `verify` confirms every entry in `report.csv` resolves to a non-empty Linear document.
5. The migration is interruptible and resumable without duplicate work.
