# tests/test_transform.py
from coda2linear.transform import (
    extract_asset_urls,
    is_gif,
    is_coda_url,
    is_external_gif_url,
    should_rehost,
)


def test_extract_inline_image():
    md = "Some text ![alt text](https://codahosted.io/docs/abc/blobs/img.png) more text"
    assert extract_asset_urls(md) == ["https://codahosted.io/docs/abc/blobs/img.png"]


def test_extract_html_img_tag():
    md = '<img src="https://codahosted.io/docs/abc/blobs/img.png" alt="x" />'
    assert extract_asset_urls(md) == ["https://codahosted.io/docs/abc/blobs/img.png"]


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


def test_should_not_rehost_external_non_gif():
    assert should_rehost("https://external-site.com/image.png") is False


def test_should_not_rehost_regular_link():
    assert should_rehost("https://example.com/page") is False
