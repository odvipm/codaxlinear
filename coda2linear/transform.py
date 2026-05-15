import re
from urllib.parse import urlparse

# Regex patterns for asset URL extraction
_INLINE_IMAGE_RE = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')
_HTML_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
_REF_DEF_RE = re.compile(r'^\[([^\]]+)\]:\s*(\S+)', re.MULTILINE)
_INLINE_IMAGE_REF_RE = re.compile(r'!\[[^\]]*\]\[([^\]]+)\]')
_CODA_LINK_RE = re.compile(r'\[[^\]]+\]\((https://codahosted\.io[^)]+)\)')

CODA_HOST = "codahosted.io"
EXTERNAL_GIF_HOSTS = frozenset({
    "giphy.com",
    "media.giphy.com",
    "tenor.com",
    "media.tenor.com",
    "media1.tenor.com",
    "media2.tenor.com",
    "i.imgur.com",
    "imgur.com",
})


def extract_asset_urls(markdown: str) -> list[str]:
    """Return ordered, deduplicated list of all asset URLs in the markdown."""
    urls: list[str] = []

    # inline images: ![alt](url)
    urls.extend(_INLINE_IMAGE_RE.findall(markdown))

    # raw HTML img tags: <img src="url">
    urls.extend(_HTML_IMG_RE.findall(markdown))

    # reference-style images: build ref map, then resolve used refs
    ref_defs = dict(_REF_DEF_RE.findall(markdown))
    for ref in _INLINE_IMAGE_REF_RE.findall(markdown):
        if ref in ref_defs:
            urls.append(ref_defs[ref])

    # Coda file attachment links: [text](https://codahosted.io/...)
    for url in _CODA_LINK_RE.findall(markdown):
        urls.append(url)

    # deduplicate, preserving first-occurrence order
    seen: set[str] = set()
    result: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def is_gif(url: str, content_type: str = "") -> bool:
    """True if url or content_type indicates a GIF."""
    if content_type:
        ct = content_type.lower().split(";")[0].strip()
        if ct == "image/gif":
            return True
    # strip querystring before checking extension
    path = urlparse(url).path.lower()
    return path.endswith(".gif")


def is_coda_url(url: str) -> bool:
    return CODA_HOST in urlparse(url).netloc


def is_external_gif_url(url: str, content_type: str = "") -> bool:
    host = urlparse(url).netloc.lower()
    return is_gif(url, content_type) and any(h in host for h in EXTERNAL_GIF_HOSTS)


def should_rehost(url: str, content_type: str = "") -> bool:
    """True if this URL should be downloaded and re-uploaded to Linear."""
    return is_coda_url(url) or is_external_gif_url(url, content_type)


def rewrite_asset_urls(markdown: str, url_map: dict[str, str]) -> str:
    """Replace each key URL with its value URL throughout the markdown body."""
    for old_url, new_url in url_map.items():
        markdown = markdown.replace(old_url, new_url)
    return markdown


def build_title(page_name: str, parent_names: list[str]) -> str:
    """Build a hierarchy-prefixed title: 'Grandparent / Parent / Page'."""
    return " / ".join(parent_names + [page_name])


def oversized_asset_callout(original_url: str) -> str:
    """Markdown callout inserted when an asset exceeds Linear's 25 MB limit."""
    return f"\n> ⚠ Asset exceeds Linear size limit; original Coda link retained: {original_url}\n"


def external_gif_fallback_callout(original_url: str) -> str:
    """Markdown callout inserted when a third-party GIF could not be rehosted."""
    return f"\n> ⚠ External GIF could not be rehosted; original link retained: {original_url}\n"


def count_table_dimensions(table_block: str) -> tuple[int, int]:
    """Return (num_columns, num_data_rows) for a Markdown table block.

    The separator row (---|---) is not counted as a data row.
    """
    lines = [l for l in table_block.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return (0, 0)
    # count non-empty cells in header row
    header_cols = len([c for c in lines[0].split("|") if c.strip()])
    # header row + separator = 2 lines; rest are data rows
    data_rows = max(len(lines) - 2, 0)
    return (header_cols, data_rows)
