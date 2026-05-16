import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse

# Regex patterns for asset URL extraction
_INLINE_IMAGE_RE = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')
_HTML_IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)
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
ASSET_EXTENSIONS = frozenset({
    ".apng",
    ".avif",
    ".bmp",
    ".doc",
    ".docx",
    ".gif",
    ".heic",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".svg",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
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


def extract_html_asset_urls(html: str) -> list[str]:
    """Return ordered, deduplicated image URLs from exported HTML."""
    seen: set[str] = set()
    result: list[str] = []
    for url in _HTML_IMG_RE.findall(html):
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


class _HtmlToMarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.link_stack: list[str | None] = []
        self.in_table = False
        self.table_rows: list[list[str]] = []
        self.current_row: list[str] | None = None
        self.current_cell_parts: list[str] | None = None
        self.list_stack: list[dict[str, int | str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): v or "" for k, v in attrs}
        tag = tag.lower()
        if tag == "table":
            self._break()
            self.in_table = True
            self.table_rows = []
        elif self.in_table:
            self._handle_table_starttag(tag, attr)
        elif tag in {"p", "div", "section", "article"}:
            self._break()
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._break()
            self._append("#" * int(tag[1]) + " ")
        elif tag == "br":
            self._append("\n")
        elif tag in {"strong", "b"}:
            self._append("**")
        elif tag in {"em", "i"}:
            self._append("*")
        elif tag in {"ol", "ul"}:
            if not self.list_stack:
                self._break()
            elif tag == "ul":
                self._break(single=True)
            self.list_stack.append({"type": tag, "counter": 0})
        elif tag == "li":
            self._start_list_item()
        elif tag == "a":
            href = attr.get("href")
            self.link_stack.append(href)
            if href:
                self._append("[")
        elif tag == "img":
            src = attr.get("src")
            if src:
                alt = attr.get("alt", "")
                self._break()
                self._append(f"![{alt}]({src})")
                self._break()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.in_table:
            self._handle_table_endtag(tag)
        elif tag in {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"}:
            if tag in {"ul", "ol"} and self.list_stack:
                self.list_stack.pop()
            self._break(single=bool(self.list_stack))
        elif tag in {"strong", "b"}:
            self._append("**")
        elif tag in {"em", "i"}:
            self._append("*")
        elif tag == "li":
            self._break(single=True)
        elif tag == "a" and self.link_stack:
            href = self.link_stack.pop()
            if href:
                self._append(f"]({href})")

    def handle_data(self, data: str) -> None:
        text = unescape(data)
        if text.strip():
            self._append(re.sub(r"\s+", " ", text))

    def _append(self, text: str) -> None:
        if self.in_table and self.current_cell_parts is not None:
            self.current_cell_parts.append(text)
        else:
            self.parts.append(text)

    def _start_list_item(self) -> None:
        if not self.list_stack:
            self._break(single=True)
            self._append("- ")
            return

        current_list = self.list_stack[-1]
        depth = len(self.list_stack)
        marker: str
        if current_list["type"] == "ol":
            current_list["counter"] = int(current_list["counter"]) + 1
            marker = f"{current_list['counter']}. "
            if depth == 1 and int(current_list["counter"]) > 1:
                self._break()
            else:
                self._break(single=True)
        else:
            marker = "- "
            self._break(single=True)

        self._append("   " * (depth - 1) + marker)

    def _handle_table_starttag(self, tag: str, attr: dict[str, str]) -> None:
        if tag == "tr":
            self.current_row = []
        elif tag in {"td", "th"}:
            self.current_cell_parts = []
        elif tag == "br":
            self._append("<br>")
        elif tag in {"strong", "b"}:
            self._append("**")
        elif tag in {"em", "i"}:
            self._append("*")
        elif tag == "a":
            href = attr.get("href")
            self.link_stack.append(href)
            if href:
                self._append("[")
        elif tag == "img":
            src = attr.get("src")
            if src:
                self._append(f"![{attr.get('alt', '')}]({src})")
        elif tag in {"p", "div"} and self.current_cell_parts:
            self._append(" ")

    def _handle_table_endtag(self, tag: str) -> None:
        if tag == "a" and self.link_stack:
            href = self.link_stack.pop()
            if href:
                self._append(f"]({href})")
        elif tag in {"strong", "b"}:
            self._append("**")
        elif tag in {"em", "i"}:
            self._append("*")
        elif tag in {"td", "th"} and self.current_cell_parts is not None:
            if self.current_row is not None:
                self.current_row.append(self._clean_cell("".join(self.current_cell_parts)))
            self.current_cell_parts = None
        elif tag == "tr":
            if self.current_row:
                self.table_rows.append(self.current_row)
            self.current_row = None
        elif tag == "table":
            table_markdown = self._render_table()
            self.in_table = False
            self.current_row = None
            self.current_cell_parts = None
            if table_markdown:
                self.parts.append(table_markdown)
                self._break()

    def _clean_cell(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text.replace("\n", "<br>")).strip()
        return text.replace("|", r"\|")

    def _render_table(self) -> str:
        if not self.table_rows:
            return ""
        width = max(len(row) for row in self.table_rows)
        rows = [row + [""] * (width - len(row)) for row in self.table_rows]
        header = rows[0]
        body = rows[1:]
        lines = [
            "| " + " | ".join(header) + " |",
            "|" + "|".join("---" for _ in range(width)) + "|",
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in body)
        return "\n".join(lines)

    def _break(self, single: bool = False) -> None:
        current = "".join(self.parts)
        suffix = "\n" if single else "\n\n"
        if current and not current.endswith(suffix):
            self.parts.append(suffix)

    def markdown(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def convert_html_to_markdown(html: str) -> str:
    """Convert exported Coda HTML to simple Markdown while preserving images."""
    parser = _HtmlToMarkdownParser()
    parser.feed(html)
    return parser.markdown()


_TABLE_SEPARATOR_RE = re.compile(r"^\|[ \t:|-]+\|[ \t]*$")
_TOP_LEVEL_BULLET_RE = re.compile(r"^[-*]\s+(.+?)\s*$")


def _is_table_separator(line: str) -> bool:
    return bool(_TABLE_SEPARATOR_RE.match(line.strip()))


def _looks_like_table_header(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _split_glued_table_header(line: str, next_line: str) -> tuple[str, str] | None:
    if _looks_like_table_header(line) or not _is_table_separator(next_line):
        return None
    marker = line.find("|")
    if marker <= 0:
        return None
    prefix = line[:marker].rstrip()
    header = line[marker:].strip()
    if not prefix or not _looks_like_table_header(header):
        return None
    return prefix, header


def _restore_numbered_parent_bullets(lines: list[str]) -> list[str]:
    restored: list[str] = []
    i = 0
    while i < len(lines):
        if not _TOP_LEVEL_BULLET_RE.match(lines[i]):
            restored.append(lines[i])
            i += 1
            continue

        block_lines: list[str] = []
        bullet_texts: list[str] = []
        j = i
        while j < len(lines):
            line = lines[j]
            bullet_match = _TOP_LEVEL_BULLET_RE.match(line)
            if bullet_match:
                block_lines.append(line)
                bullet_texts.append(bullet_match.group(1).strip())
                j += 1
                continue
            if not line.strip():
                block_lines.append(line)
                j += 1
                continue
            break

        heading_indexes = {
            index for index, text in enumerate(bullet_texts)
            if text.endswith(":")
        }
        if len(heading_indexes) < 2:
            restored.extend(block_lines)
            i = j
            continue

        number = 1
        for index, text in enumerate(bullet_texts):
            if index in heading_indexes:
                if number > 1:
                    restored.append("")
                restored.append(f"{number}. {text}")
                number += 1
            else:
                restored.append(f"   - {text}")
        i = j

    return restored


def normalize_markdown_for_linear(markdown: str) -> str:
    """Clean Markdown edge cases that prevent Linear from rendering blocks."""
    raw_lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    expanded: list[str] = []
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i].rstrip()
        next_line = raw_lines[i + 1].rstrip() if i + 1 < len(raw_lines) else ""
        split = _split_glued_table_header(line, next_line)
        if split:
            prefix, header = split
            expanded.append(prefix)
            expanded.append("")
            expanded.append(header)
        else:
            expanded.append(line)
        i += 1

    normalized: list[str] = []
    for i, line in enumerate(expanded):
        prev_line = normalized[-1] if normalized else ""
        next_line = expanded[i + 1] if i + 1 < len(expanded) else ""
        starts_table = _looks_like_table_header(line) and _is_table_separator(next_line)

        if starts_table and normalized and prev_line.strip():
            normalized.append("")
        normalized.append(line)

        ends_table = (
            line.strip().startswith("|")
            and line.strip().endswith("|")
            and (
                i + 1 == len(expanded)
                or not expanded[i + 1].strip().startswith("|")
            )
        )
        if ends_table and i + 1 < len(expanded) and expanded[i + 1].strip():
            normalized.append("")

    normalized = _restore_numbered_parent_bullets(normalized)

    text = "\n".join(normalized)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
    """True if an extracted asset URL should be re-uploaded to Linear."""
    path = urlparse(url).path.lower()
    return (
        is_coda_url(url)
        or is_external_gif_url(url, content_type)
        or any(path.endswith(ext) for ext in ASSET_EXTENSIONS)
    )


def rewrite_asset_urls(markdown: str, url_map: dict[str, str]) -> str:
    """Replace each key URL with its value URL throughout the markdown body."""
    for old_url, new_url in url_map.items():
        markdown = markdown.replace(old_url, new_url)
    return markdown


def rewrite_coda_page_links(markdown: str, url_map: dict[str, str]) -> str:
    """Replace known Coda page URLs/IDs in Markdown with Linear document URLs."""
    for old_url, linear_url in sorted(url_map.items(), key=lambda item: len(item[0]), reverse=True):
        if old_url.startswith(("http://", "https://")):
            markdown = markdown.replace(old_url, linear_url)
        else:
            markdown = re.sub(
                rf"https://[^\s)>\]]*{re.escape(old_url)}[^\s)>\]]*",
                linear_url,
                markdown,
            )
    return markdown


def build_title(
    page_name: str,
    parent_names: list[str],
    title_root: str | None = None,
    include_parents: bool = False,
) -> str:
    """Build a hierarchy-prefixed title, optionally starting at title_root."""
    if not include_parents:
        return page_name

    names = parent_names
    if title_root:
        try:
            root_index = names.index(title_root)
            names = names[root_index:]
        except ValueError:
            pass
    return " / ".join(names + [page_name])


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
