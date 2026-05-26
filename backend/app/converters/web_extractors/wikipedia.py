from copy import deepcopy
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.converters.web_extractors.base import WebExtractorContext, WebExtractorResult
from app.converters.web_extractors.utils import clean_markdown, markdown_from_node, normalize_links, normalize_text, visible_text


def extract(context: WebExtractorContext) -> WebExtractorResult | None:
    if not is_wikipedia_site(context.base_url) or not has_wikipedia_article_content(context.soup):
        return None
    body = wikipedia_article_markdown(context.soup, context.base_url, context.title)
    if not body:
        return None
    return WebExtractorResult(
        name="wikipedia-article",
        body=body,
        score=930,
        metadata={"length": len(body)},
    )


def is_wikipedia_site(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    return host == "wikipedia.org" or host.endswith(".wikipedia.org")


def has_wikipedia_article_content(soup: BeautifulSoup) -> bool:
    content = soup.select_one("#mw-content-text .mw-parser-output")
    return content is not None and len(visible_text(content)) >= 100


def wikipedia_article_markdown(soup: BeautifulSoup, base_url: str, title: str) -> str | None:
    content = soup.select_one("#mw-content-text .mw-parser-output")
    if not content or len(visible_text(content)) < 100:
        return None

    content_copy = deepcopy(content)
    clean_wikipedia_content(content_copy)
    normalize_links(content_copy, base_url)
    normalize_wikipedia_media(content_copy, base_url)
    media = wikipedia_media(content_copy)
    markdown = clean_wikipedia_markdown(clean_markdown(markdown_from_node(content_copy), title), title)
    if len(markdown) < 100:
        return None

    sections = []
    article_title = wikipedia_title(soup, title)
    if article_title:
        sections.append(f"# {article_title}")
    summary = wikipedia_summary(markdown)
    if summary:
        sections.append("## 摘要\n\n" + summary)
    sections.append("## 正文\n\n" + markdown)
    if media:
        sections.append("## 图片资源\n\n" + "\n".join(f"- [{label}]({url})" for label, url in media))
    return "\n\n".join(sections).strip()


def clean_wikipedia_content(node) -> None:
    for tag in list(
        node.select(
            "style, script, link, meta, .mw-editsection, .reference, .noprint, .printfooter, "
            ".metadata, .ambox, .tmbox, .cmbox, .ombox, .imbox, .fmbox, .mbox-small, "
            ".navbox, .vertical-navbox, .sidebar, .toc, #toc, .mw-collapsible-toggle, "
            ".navbar, .catlinks, .portal, .sistersitebox, .authority-control, .reflist, .references, "
            ".mw-empty-elt, .mw-jump-link, .mw-indicators, .uls-settings-trigger"
        )
    ):
        if tag.parent is not None:
            tag.decompose()

    for tag in list(node.select(".hatnote")):
        if len(visible_text(tag)) < 220 and tag.parent is not None:
            tag.decompose()

    for tag in node.find_all(True):
        if tag.name == "sup":
            tag.decompose()
            continue
        tag.attrs = {
            key: value
            for key, value in tag.attrs.items()
            if key in {"href", "src", "srcset", "alt", "title", "colspan", "rowspan"}
        }


def normalize_wikipedia_media(node, base_url: str) -> None:
    for tag in node.find_all(["img", "source"]):
        for attr in ["src", "srcset"]:
            value = tag.get(attr)
            if not isinstance(value, str) or not value.strip():
                continue
            if attr == "srcset":
                tag[attr] = ", ".join(normalize_srcset_entry(entry, base_url) for entry in value.split(","))
            else:
                tag[attr] = normalize_wikipedia_url(value.strip(), base_url)


def normalize_srcset_entry(entry: str, base_url: str) -> str:
    parts = entry.strip().split()
    if not parts:
        return ""
    parts[0] = normalize_wikipedia_url(parts[0], base_url)
    return " ".join(parts)


def normalize_wikipedia_url(value: str, base_url: str) -> str:
    if value.startswith("//"):
        return "https:" + value
    return urljoin(base_url, value)


def wikipedia_media(node) -> list[tuple[str, str]]:
    media: list[tuple[str, str]] = []
    seen: set[str] = set()
    for image in node.find_all("img"):
        src = image.get("src")
        if not isinstance(src, str) or not src.strip() or src in seen:
            continue
        seen.add(src)
        label = normalize_text(image.get("alt") or image.get("title") or src.rsplit("/", 1)[-1])
        media.append((label or "image", src))
        if len(media) >= 20:
            break
    return media


def wikipedia_title(soup: BeautifulSoup, fallback_title: str) -> str:
    for selector in [".mw-page-title-main", "#firstHeading", "h1"]:
        node = soup.select_one(selector)
        if node:
            text = normalize_text(node.get_text(" ", strip=True))
            if text:
                return text
    return normalize_text(fallback_title)


def wikipedia_summary(markdown: str) -> str:
    lines = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines:
                break
            continue
        if stripped.startswith("#"):
            break
        if stripped.startswith("|"):
            continue
        lines.append(stripped)
        if len(" ".join(lines)) >= 500:
            break
    return " ".join(lines).strip()


def clean_wikipedia_markdown(markdown: str, title: str) -> str:
    text = markdown
    text = text.replace("[编辑]", "")
    text = text.replace("[ 编辑 ]", "")
    text = text.replace("↑", "")
    text = clean_markdown(text, title)
    return text.strip()
