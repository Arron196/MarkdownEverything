from app.markdown import render_document_markdown, render_media_markdown


def test_render_document_markdown_has_required_sections():
    markdown = render_document_markdown(
        title="Example",
        source_type="webpage",
        source_url="https://example.com",
        body="Body",
        summary="Summary",
    )
    assert "---" in markdown
    assert "# Example" in markdown
    assert "## 摘要" in markdown
    assert "## 正文" in markdown
    assert "原始链接：https://example.com" in markdown


def test_render_media_markdown_has_timeline():
    markdown = render_media_markdown(
        title="Audio",
        source_type="audio",
        summary="Summary",
        timeline=[{"start": "00:00", "end": "01:00", "title": "开场", "text": "Hello"}],
    )
    assert "## 时间轴" in markdown
    assert "### 00:00 - 01:00 开场" in markdown

