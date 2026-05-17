# tests/test_transform.py
from coda2linear.transform import (
    convert_html_to_markdown,
    extract_asset_urls,
    extract_html_asset_urls,
    is_gif,
    is_coda_url,
    is_external_gif_url,
    should_rehost,
    rewrite_asset_urls,
    rewrite_coda_page_links,
    normalize_markdown_for_linear,
    build_title,
    oversized_asset_callout,
    external_gif_fallback_callout,
    count_table_dimensions,
)


def test_extract_inline_image():
    md = "Some text ![alt text](https://codahosted.io/docs/abc/blobs/img.png) more text"
    assert extract_asset_urls(md) == ["https://codahosted.io/docs/abc/blobs/img.png"]


def test_extract_html_img_tag():
    md = '<img src="https://codahosted.io/docs/abc/blobs/img.png" alt="x" />'
    assert extract_asset_urls(md) == ["https://codahosted.io/docs/abc/blobs/img.png"]


def test_extract_html_asset_urls_single_and_double_quotes():
    html = """
    <p>Before</p>
    <img src="https://codahosted.io/docs/abc/blobs/a.png" />
    <img src='https://media.giphy.com/media/abc/giphy.gif' />
    """
    assert extract_html_asset_urls(html) == [
        "https://codahosted.io/docs/abc/blobs/a.png",
        "https://media.giphy.com/media/abc/giphy.gif",
    ]


def test_convert_html_to_markdown_preserves_image_position():
    html = """
    <h1>Setup</h1>
    <p>Before image</p>
    <img src="https://codahosted.io/img.png" alt="Screenshot" />
    <p>After <a href="https://coda.io/d/doc/page">link</a></p>
    """
    assert convert_html_to_markdown(html) == (
        "# Setup\n\n"
        "Before image\n\n"
        "![Screenshot](https://codahosted.io/img.png)\n\n"
        "After [link](https://coda.io/d/doc/page)"
    )


def test_convert_html_to_markdown_preserves_inline_emphasis():
    html = """
    <p>Use <strong>strong text</strong>, <em>italic text</em>, and <strong><em>both</em></strong>.</p>
    <p>Also <b>bold</b> and <i>italic</i>.</p>
    """

    assert convert_html_to_markdown(html) == (
        "Use **strong text**, *italic text*, and ***both***.\n\n"
        "Also **bold** and *italic*."
    )


def test_convert_html_to_markdown_preserves_code_strike_quote_and_divider():
    html = """
    <p>Run <code>coda2linear migrate</code> then <s>ignore this</s>.</p>
    <blockquote><p>Important note</p><p>Second line</p></blockquote>
    <hr>
    <pre><code>first line
second line</code></pre>
    """

    assert convert_html_to_markdown(html) == (
        "Run `coda2linear migrate` then ~ignore this~.\n\n"
        "> Important note\n"
        "> Second line\n\n"
        "___\n\n"
        "```\n"
        "first line\n"
        "second line\n"
        "```"
    )


def test_convert_html_to_markdown_preserves_checklists():
    html = """
    <ul>
      <li><input type="checkbox" checked> Done item</li>
      <li><input type="checkbox"> Pending item</li>
    </ul>
    """

    assert convert_html_to_markdown(html) == (
        "- [x] Done item\n"
        "- [ ] Pending item"
    )


def test_convert_html_to_markdown_preserves_table_structure():
    html = """
    <p>Before table</p>
    <table>
      <thead>
        <tr><th>Status</th><th>Owner</th></tr>
      </thead>
      <tbody>
        <tr><td>Open</td><td><a href="https://coda.io/d/doc/page">Team A</a></td></tr>
        <tr><td>Blocked | urgent</td><td>Team B</td></tr>
      </tbody>
    </table>
    <p>After table</p>
    """
    assert convert_html_to_markdown(html) == (
        "Before table\n\n"
        "| Status | Owner |\n"
        "|---|---|\n"
        "| Open | [Team A](https://coda.io/d/doc/page) |\n"
        "| Blocked \\| urgent | Team B |\n\n"
        "After table"
    )


def test_convert_html_to_markdown_preserves_ordered_list_with_nested_bullets():
    html = """
    <ol>
      <li>Pouch Creation:
        <ul>
          <li>Gather the items to be sent to different departments in Head Office.</li>
          <li>Create a pouch and encode the list of items into the Pouch Receiving System.</li>
        </ul>
      </li>
      <li>Pouch Handling to Courier:
        <ul>
          <li>Send the pouch to the designated courier for dispatch.</li>
          <li>Pouch status will be "In Transit".</li>
        </ul>
      </li>
    </ol>
    """

    assert convert_html_to_markdown(html) == (
        "1. Pouch Creation:\n"
        "   - Gather the items to be sent to different departments in Head Office.\n"
        "   - Create a pouch and encode the list of items into the Pouch Receiving System.\n\n"
        "2. Pouch Handling to Courier:\n"
        "   - Send the pouch to the designated courier for dispatch.\n"
        "   - Pouch status will be \"In Transit\"."
    )


def test_normalize_markdown_for_linear_separates_table_from_previous_text():
    md = (
        "List or Department Receivers| Full Name | Email |\n"
        "|---|---|\n"
        "| Rosario Ogao | rogao@pcni.com.ph |\n"
        "\n"
        "Issue Handling"
    )

    assert normalize_markdown_for_linear(md) == (
        "List or Department Receivers\n\n"
        "| Full Name | Email |\n"
        "|---|---|\n"
        "| Rosario Ogao | rogao@pcni.com.ph |\n\n"
        "Issue Handling"
    )


def test_normalize_markdown_for_linear_keeps_existing_table_spacing():
    md = (
        "Before\n\n"
        "| Full Name | Email |\n"
        "|---|---|\n"
        "| Rosario Ogao | rogao@pcni.com.ph |\n\n"
        "After"
    )

    assert normalize_markdown_for_linear(md) == md


def test_normalize_markdown_for_linear_restores_numbered_parent_bullets():
    md = (
        "- Pouch Creation:\n"
        "- Gather the items to be sent to different departments in Head Office.\n"
        "- Create a pouch and encode the list of items into the Pouch Receiving System.\n"
        "- Pouch status will be \"Pending\".\n"
        "- Pouch Handling to Courier:\n"
        "- Send the pouch to the designated courier for dispatch.\n"
        "- Courier gives a tracking number to the pouch."
    )

    assert normalize_markdown_for_linear(md) == (
        "1. Pouch Creation:\n"
        "   - Gather the items to be sent to different departments in Head Office.\n"
        "   - Create a pouch and encode the list of items into the Pouch Receiving System.\n"
        "   - Pouch status will be \"Pending\".\n\n"
        "2. Pouch Handling to Courier:\n"
        "   - Send the pouch to the designated courier for dispatch.\n"
        "   - Courier gives a tracking number to the pouch."
    )


def test_normalize_markdown_for_linear_indents_bullets_nested_in_numbered_list():
    md = (
        "Type of Items\n\n"
        "1. ATM\n"
        "2. Document\n"
        "3. Confidential Document\n"
        "  * Receipts, Anything that is related to cash/money, Collections, Loan Docs, Demand Letters\n"
        "4. Computer Parts/Accessories\n"
        "  * Mouse, Keyboard, Webcam, Monitor, CPU, etc\n"
        "5. Computer Set\n"
        "6. CCTV Assets\n"
        "7. Mobile Device\n"
        "8. Mobile Device Accessories\n"
        "  * Charger, Headset, Batteries, etc\n"
        "9. Printer"
    )

    assert normalize_markdown_for_linear(md) == (
        "Type of Items\n\n"
        "1. ATM\n"
        "2. Document\n"
        "3. Confidential Document\n"
        "   * Receipts, Anything that is related to cash/money, Collections, Loan Docs, Demand Letters\n"
        "4. Computer Parts/Accessories\n"
        "   * Mouse, Keyboard, Webcam, Monitor, CPU, etc\n"
        "5. Computer Set\n"
        "6. CCTV Assets\n"
        "7. Mobile Device\n"
        "8. Mobile Device Accessories\n"
        "   * Charger, Headset, Batteries, etc\n"
        "9. Printer"
    )


def test_extract_reference_style_image():
    md = "![alt text][ref1]\n\n[ref1]: https://codahosted.io/docs/abc/blobs/img.png"
    assert extract_asset_urls(md) == ["https://codahosted.io/docs/abc/blobs/img.png"]


def test_extract_coda_file_attachment_link():
    md = "[report.pdf](https://codahosted.io/docs/abc/blobs/report.pdf)"
    assert extract_asset_urls(md) == ["https://codahosted.io/docs/abc/blobs/report.pdf"]


def test_extract_external_gif_inline():
    md = "![funny](https://media.giphy.com/media/abc/giphy.gif)"
    assert extract_asset_urls(md) == ["https://media.giphy.com/media/abc/giphy.gif"]


def test_extract_deduplicates_same_url():
    md = "![a](https://codahosted.io/x.png) ![b](https://codahosted.io/x.png)"
    assert extract_asset_urls(md) == ["https://codahosted.io/x.png"]


def test_extract_non_coda_regular_link_excluded():
    md = "[click here](https://example.com/page)"
    assert extract_asset_urls(md) == []


def test_extract_order_preserved():
    md = "![a](https://codahosted.io/a.png)\n![b](https://codahosted.io/b.png)"
    assert extract_asset_urls(md) == [
        "https://codahosted.io/a.png",
        "https://codahosted.io/b.png",
    ]


def test_is_gif_by_extension():
    assert is_gif("https://media.giphy.com/media/abc/giphy.gif") is True


def test_is_gif_by_extension_with_querystring():
    assert is_gif("https://media.giphy.com/media/abc/giphy.gif?cid=123&rid=xyz") is True


def test_is_gif_by_content_type():
    assert is_gif("https://example.com/image", "image/gif") is True


def test_is_gif_content_type_with_charset():
    assert is_gif("https://example.com/image", "image/gif; charset=utf-8") is True


def test_is_gif_false_for_jpg():
    assert is_gif("https://example.com/photo.jpg") is False


def test_is_gif_false_for_png():
    assert is_gif("https://example.com/photo.png", "image/png") is False


def test_is_coda_url_true():
    assert is_coda_url("https://codahosted.io/docs/abc/blobs/img.png") is True


def test_is_coda_url_false():
    assert is_coda_url("https://giphy.com/media/abc/giphy.gif") is False


def test_is_external_gif_url_giphy():
    assert is_external_gif_url("https://media.giphy.com/media/abc/giphy.gif") is True


def test_is_external_gif_url_tenor():
    assert is_external_gif_url("https://media.tenor.com/abc/tenor.gif") is True


def test_is_external_gif_url_false_for_non_gif():
    assert is_external_gif_url("https://giphy.com/image.png") is False


def test_is_external_gif_url_false_for_unknown_host():
    assert is_external_gif_url("https://example.com/animation.gif") is False


def test_should_rehost_coda_image():
    assert should_rehost("https://codahosted.io/img.png") is True


def test_should_rehost_coda_pdf():
    assert should_rehost("https://codahosted.io/report.pdf") is True


def test_should_rehost_external_gif():
    assert should_rehost("https://media.giphy.com/media/abc/giphy.gif") is True


def test_should_rehost_exported_s3_image():
    url = (
        "https://coda-us-west-2-prod-workflow-objects.s3.us-west-2.amazonaws.com/"
        "DOC_EXPORT_RENDERING/image.png?X-Amz-Signature=abc"
    )
    assert should_rehost(url) is True


def test_should_rehost_external_non_gif_image():
    assert should_rehost("https://external-site.com/image.png") is True


def test_should_not_rehost_regular_link():
    assert should_rehost("https://example.com/page") is False


def test_rewrite_inline_image_url():
    md = "![alt](https://codahosted.io/img.png)"
    result = rewrite_asset_urls(md, {"https://codahosted.io/img.png": "https://linear.app/assets/img.png"})
    assert result == "![alt](https://linear.app/assets/img.png)"


def test_rewrite_multiple_urls():
    md = "![a](https://codahosted.io/a.png) ![b](https://codahosted.io/b.png)"
    url_map = {
        "https://codahosted.io/a.png": "https://linear.app/a.png",
        "https://codahosted.io/b.png": "https://linear.app/b.png",
    }
    result = rewrite_asset_urls(md, url_map)
    assert "https://linear.app/a.png" in result
    assert "https://linear.app/b.png" in result
    assert "codahosted.io" not in result


def test_rewrite_empty_map_returns_unchanged():
    md = "![alt](https://codahosted.io/img.png)"
    assert rewrite_asset_urls(md, {}) == md


def test_rewrite_url_appearing_multiple_times():
    md = "![a](https://codahosted.io/x.png) ![b](https://codahosted.io/x.png)"
    result = rewrite_asset_urls(md, {"https://codahosted.io/x.png": "https://linear.app/x.png"})
    assert result.count("https://linear.app/x.png") == 2
    assert "codahosted.io" not in result


def test_rewrite_coda_page_links_to_linear_urls():
    md = (
        "[Guide](https://coda.io/d/doc/Guide_suABC#canvas-X0lOuXfg62) "
        "and https://coda.io/d/doc/Other_suDEF"
    )
    result = rewrite_coda_page_links(
        md,
        {
            "https://coda.io/d/doc/Guide_suABC#canvas-X0lOuXfg62": "https://linear.app/doc-guide",
            "https://coda.io/d/doc/Other_suDEF": "https://linear.app/doc-other",
        },
    )
    assert result == "[Guide](https://linear.app/doc-guide) and https://linear.app/doc-other"


def test_rewrite_coda_page_links_by_page_id_inside_url():
    md = "[Guide](https://coda.io/d/doc/Guide_suABC#canvas-X0lOuXfg62)"
    result = rewrite_coda_page_links(md, {"canvas-X0lOuXfg62": "https://linear.app/doc-guide"})
    assert result == "[Guide](https://linear.app/doc-guide)"


def test_build_title_no_parents():
    assert build_title("My Page", []) == "My Page"


def test_build_title_one_parent():
    assert build_title("Setup", ["Onboarding"]) == "Setup"


def test_build_title_deep_hierarchy():
    assert build_title("Setup", ["Onboarding", "Day 1"]) == "Setup"


def test_build_title_can_include_parents():
    assert (
        build_title("Setup", ["Onboarding", "Day 1"], include_parents=True)
        == "Onboarding / Day 1 / Setup"
    )


def test_build_title_can_start_at_named_root():
    result = build_title(
        "Overview",
        ["Digital Transformation Team", "Projects", "Pouch Receiving System"],
        title_root="Pouch Receiving System",
        include_parents=True,
    )
    assert result == "Pouch Receiving System / Overview"


def test_build_title_root_not_found_keeps_full_hierarchy():
    result = build_title(
        "Overview",
        ["Digital Transformation Team", "Projects", "Pouch Receiving System"],
        title_root="Other Project",
        include_parents=True,
    )
    assert result == "Digital Transformation Team / Projects / Pouch Receiving System / Overview"


def test_build_title_single_name():
    assert build_title("Home", []) == "Home"


def test_oversized_asset_callout_contains_warning_and_url():
    result = oversized_asset_callout("https://codahosted.io/large.gif")
    assert "⚠" in result
    assert "codahosted.io/large.gif" in result
    assert result.startswith("\n>")


def test_external_gif_fallback_callout_contains_warning_and_url():
    result = external_gif_fallback_callout("https://media.giphy.com/abc/giphy.gif")
    assert "⚠" in result
    assert "giphy.com" in result
    assert result.startswith("\n>")


def test_count_table_dimensions_two_columns_two_rows():
    table = "| Col A | Col B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    cols, rows = count_table_dimensions(table)
    assert cols == 2
    assert rows == 2


def test_count_table_dimensions_three_columns_one_row():
    table = "| A | B | C |\n|---|---|---|\n| x | y | z |"
    cols, rows = count_table_dimensions(table)
    assert cols == 3
    assert rows == 1


def test_count_table_dimensions_empty_string():
    assert count_table_dimensions("") == (0, 0)


def test_count_table_dimensions_header_only():
    table = "| A | B |\n|---|---|"
    cols, rows = count_table_dimensions(table)
    assert cols == 2
    assert rows == 0
