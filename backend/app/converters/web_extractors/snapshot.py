import re
from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Comment

from app.config import settings
from app.converters.web_extractors.base import WebExtractorContext, WebExtractorResult
from app.converters.web_extractors.utils import normalize_links, normalize_text, unique_preserve_order, visible_text
from app.converters.web_extractors.utils import extract_image_source, image_label_from_url, is_placeholder_image


@dataclass
class PageSnapshot:
    title: str
    description: str | None
    headings: list[str]
    text_blocks: list[str]
    controls: list[str]
    links: list[tuple[str, str]]
    images: list[tuple[str, str]]


def extract(context: WebExtractorContext) -> WebExtractorResult:
    snapshot = build_page_snapshot(context.soup, context.base_url, context.title)
    return WebExtractorResult(
        name="rendered-snapshot" if context.rendered else "static-snapshot",
        body=render_page_snapshot_markdown(snapshot),
        score=100 if context.rendered else 50,
    )


def build_page_snapshot(soup: BeautifulSoup, base_url: str, title: str) -> PageSnapshot:
    visible_root = deepcopy(soup.body or soup)
    clean_snapshot_node(visible_root)
    normalize_links(visible_root, base_url)
    description = metadata_value(soup, "description") or metadata_value(soup, "og:description")
    headings = unique_preserve_order(
        normalize_text(tag.get_text(" ", strip=True))
        for tag in visible_root.find_all(re.compile(r"^h[1-6]$"))
        if normalize_text(tag.get_text(" ", strip=True))
    )[:30]
    controls = extract_controls(visible_root)
    text_blocks = extract_text_blocks(visible_root)
    links = extract_snapshot_links(visible_root)
    images = extract_snapshot_images(visible_root, base_url)
    return PageSnapshot(
        title=title,
        description=description,
        headings=headings,
        text_blocks=text_blocks,
        controls=controls,
        links=links,
        images=images,
    )


def metadata_value(soup: BeautifulSoup, key: str) -> str | None:
    tag = soup.find("meta", attrs={"name": key}) or soup.find("meta", attrs={"property": key})
    if not tag:
        return None
    content = tag.get("content")
    return content.strip() if isinstance(content, str) else None


def clean_snapshot_node(node) -> None:
    for comment in node.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    for tag in list(
        node.select(
            "script, style, noscript, template, svg, iframe, object, embed, canvas, "
            "[hidden], [aria-hidden='true'], [style*='display:none'], [style*='display: none'], "
            "[style*='visibility:hidden'], [style*='visibility: hidden']"
        )
    ):
        if tag.parent is not None:
            tag.decompose()


def extract_controls(node) -> list[str]:
    controls: list[str] = []
    for tag in node.find_all(["input", "textarea", "select", "button"]):
        if is_invisible_control(tag):
            continue
        label = control_label(tag)
        if not label:
            continue
        control_type = tag.name
        if tag.name == "input":
            control_type = f"input:{tag.get('type', 'text')}"
        controls.append(f"{control_type} - {label}")
    return unique_preserve_order(controls)[:40]


def is_invisible_control(tag) -> bool:
    input_type = normalize_text(tag.get("type", "")).lower()
    if input_type == "hidden":
        return True
    if input_type in {"submit", "reset", "button", "image"} and not control_label(tag):
        return True
    return bool(tag.find_parent(attrs={"aria-hidden": "true"}))


def control_label(tag) -> str:
    values = [
        tag.get("aria-label"),
        tag.get("placeholder"),
        tag.get("title"),
        tag.get("alt"),
        tag.get("value") if tag.name == "button" else None,
        tag.get("value") if tag.name == "input" and tag.get("type") in {"submit", "button", "reset"} else None,
        tag.get_text(" ", strip=True),
    ]
    for value in values:
        if isinstance(value, str) and normalize_text(value):
            return normalize_text(value)
    if tag.name == "input" and tag.get("type") in {"search", "text", "url", "email", "tel", "number", "password"}:
        field_name = normalize_text(tag.get("name") or tag.get("id") or "")
        if field_name.lower() in {"q", "query", "search", "keyword", "keywords"}:
            return "搜索关键词"
        return field_name
    return ""


def extract_text_blocks(node) -> list[str]:
    blocks: list[str] = []
    for tag in node.find_all(["h1", "h2", "h3", "p", "li", "figcaption", "blockquote", "label"]):
        text = normalize_text(tag.get_text(" ", strip=True))
        if not useful_snapshot_text(text):
            continue
        blocks.append(text)
    if len(blocks) < 5:
        for text in visible_text(node).split("  "):
            if useful_snapshot_text(text):
                blocks.append(normalize_text(text))
    return unique_preserve_order(blocks)[: settings.web_snapshot_max_text_blocks]


def useful_snapshot_text(text: str) -> bool:
    if len(text) < 2:
        return False
    if len(text) > 500:
        return False
    if re.fullmatch(r"[\W_]+", text, flags=re.UNICODE):
        return False
    blocked = {"javascript", "cookie", "cookies"}
    return text.lower() not in blocked


def extract_snapshot_links(node) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for anchor in node.find_all("a"):
        href = anchor.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        text = normalize_text(anchor.get_text(" ", strip=True) or anchor.get("aria-label") or anchor.get("title") or "")
        if not text:
            continue
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https", "mailto", "tel"} and not href.startswith("#"):
            continue
        item = (text[:120], compact_url(href))
        if item in seen:
            continue
        seen.add(item)
        links.append(item)
        if len(links) >= settings.web_snapshot_max_links:
            break
    return links


def extract_snapshot_images(node, base_url: str) -> list[tuple[str, str]]:
    images: list[tuple[str, str]] = []
    seen: set[str] = set()
    for img in node.find_all("img"):
        image_url = extract_image_source(img, base_url)
        if not image_url or image_url in seen or is_placeholder_image(img, image_url):
            continue
        label = normalize_text(img.get("alt") or img.get("title") or image_label_from_url(image_url))
        seen.add(image_url)
        images.append((label[:120], image_url))
        if len(images) >= settings.web_snapshot_max_images:
            break
    return images


def render_page_snapshot_markdown(snapshot: PageSnapshot) -> str:
    sections: list[str] = []
    if snapshot.description:
        sections.append(f"## 页面描述\n\n{snapshot.description.strip()}")
    if snapshot.headings:
        sections.append("## 页面标题结构\n\n" + "\n".join(f"- {heading}" for heading in snapshot.headings))
    if snapshot.controls:
        sections.append("## 可交互控件\n\n" + "\n".join(f"- {control}" for control in snapshot.controls))
    if snapshot.text_blocks:
        sections.append("## 可见文本\n\n" + "\n".join(f"- {text}" for text in snapshot.text_blocks))
    if snapshot.links:
        sections.append("## 主要链接\n\n" + "\n".join(f"- [{text}]({href})" for text, href in snapshot.links))
    if snapshot.images:
        sections.append("## 图片\n\n" + "\n".join(f"- [{label}]({src})" for label, src in snapshot.images))
    if not sections:
        sections.append("## 页面快照\n\n未检测到可提取的可见正文内容。")
    return "\n\n".join(sections)


def compact_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return value
    path = parsed.path or "/"
    query_match = re.search(r"(?:^|&)q=([^&]+)", parsed.query)
    if query_match:
        return f"{parsed.scheme}://{parsed.netloc}{path}?q={query_match.group(1)}"
    if len(value) <= 180:
        return value
    return f"{parsed.scheme}://{parsed.netloc}{path}"
