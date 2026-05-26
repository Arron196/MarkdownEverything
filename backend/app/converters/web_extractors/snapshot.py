import json
import re
from copy import deepcopy
from dataclasses import dataclass
from html import unescape
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
    metadata: list[tuple[str, str]]
    headings: list[str]
    text_blocks: list[str]
    controls: list[str]
    lists: list[list[str]]
    tables: list[dict[str, list]]
    links: list[tuple[str, str]]
    images: list[tuple[str, str]]
    media: list[tuple[str, str]]


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
    metadata = extract_snapshot_metadata(soup)
    headings = unique_preserve_order(
        normalize_text(tag.get_text(" ", strip=True))
        for tag in visible_root.find_all(re.compile(r"^h[1-6]$"))
        if normalize_text(tag.get_text(" ", strip=True))
    )[:30]
    controls = extract_controls(visible_root)
    text_blocks = extract_text_blocks(visible_root)
    lists = extract_snapshot_lists(visible_root)
    tables = extract_snapshot_tables(visible_root)
    links = extract_snapshot_links(visible_root)
    images = extract_snapshot_images(visible_root, base_url)
    media = extract_snapshot_media(visible_root, base_url)
    return PageSnapshot(
        title=title,
        description=description,
        metadata=metadata,
        headings=headings,
        text_blocks=text_blocks,
        controls=controls,
        lists=lists,
        tables=tables,
        links=links,
        images=images,
        media=media,
    )


def metadata_value(soup: BeautifulSoup, key: str) -> str | None:
    tag = soup.find("meta", attrs={"name": key}) or soup.find("meta", attrs={"property": key})
    if not tag:
        return None
    content = tag.get("content")
    return content.strip() if isinstance(content, str) else None


def extract_snapshot_metadata(soup: BeautifulSoup) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for key in [
        "og:site_name",
        "og:type",
        "article:author",
        "article:published_time",
        "twitter:site",
        "author",
        "date",
    ]:
        value = metadata_value(soup, key)
        if value:
            fields.append((key, normalize_text(value)))
    for item in extract_json_ld_items(soup):
        item_type = json_ld_type(item)
        if item_type:
            fields.append(("jsonld:type", item_type))
        for key in ["headline", "name", "author", "datePublished"]:
            value = json_ld_scalar(item.get(key))
            if value:
                fields.append((f"jsonld:{key}", value))
    return unique_preserve_order(fields)[:20]


def extract_json_ld_items(soup: BeautifulSoup) -> list[dict]:
    items: list[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text("", strip=True)
        if not text:
            continue
        try:
            parsed = json.loads(unescape(text))
        except Exception:
            continue
        if isinstance(parsed, dict):
            if isinstance(parsed.get("@graph"), list):
                items.extend(item for item in parsed["@graph"] if isinstance(item, dict))
            else:
                items.append(parsed)
        elif isinstance(parsed, list):
            items.extend(item for item in parsed if isinstance(item, dict))
    return items[:10]


def json_ld_type(item: dict) -> str | None:
    value = item.get("@type")
    if isinstance(value, list):
        return ", ".join(str(part) for part in value if part)
    if isinstance(value, str):
        return value
    return None


def json_ld_scalar(value) -> str | None:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, dict):
        for key in ["name", "@id", "url"]:
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return normalize_text(nested)
    if isinstance(value, list):
        values = [json_ld_scalar(item) for item in value[:3]]
        values = [item for item in values if item]
        if values:
            return ", ".join(values)
    return None


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
    for tag in node.find_all(["h1", "h2", "h3", "h4", "p", "li", "figcaption", "blockquote", "label", "summary", "dt", "dd"]):
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


def extract_snapshot_lists(node) -> list[list[str]]:
    lists: list[list[str]] = []
    for list_node in node.find_all(["ul", "ol"]):
        items = [
            normalize_text(item.get_text(" ", strip=True))
            for item in list_node.find_all("li", recursive=False)
        ]
        items = [item for item in items if useful_snapshot_text(item)]
        if len(items) >= 2:
            lists.append(items[:20])
        if len(lists) >= 10:
            break
    return lists


def extract_snapshot_tables(node) -> list[dict[str, list]]:
    tables: list[dict[str, list]] = []
    for table in node.find_all("table"):
        rows = []
        for row in table.find_all("tr")[:12]:
            cells = [
                normalize_text(cell.get_text(" ", strip=True))
                for cell in row.find_all(["th", "td"])
            ]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(cells[:8])
        if rows:
            tables.append({"rows": rows})
        if len(tables) >= settings.web_snapshot_max_tables:
            break
    return tables


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


def extract_snapshot_media(node, base_url: str) -> list[tuple[str, str]]:
    media: list[tuple[str, str]] = []
    seen: set[str] = set()
    for tag in node.find_all(["video", "audio", "source", "track"]):
        src = tag.get("src")
        if not isinstance(src, str) or not src.strip():
            continue
        absolute = normalize_media_url(src, base_url)
        if not absolute or absolute in seen:
            continue
        label = normalize_text(tag.get("title") or tag.get("aria-label") or tag.name)
        seen.add(absolute)
        media.append((label, absolute))
        if len(media) >= settings.web_snapshot_max_media:
            break
    return media


def normalize_media_url(src: str, base_url: str) -> str | None:
    if src.startswith(("data:", "blob:", "javascript:")):
        return None
    from urllib.parse import urljoin

    absolute = urljoin(base_url, src)
    if urlparse(absolute).scheme in {"http", "https"}:
        return absolute
    return None


def render_page_snapshot_markdown(snapshot: PageSnapshot) -> str:
    sections: list[str] = []
    if snapshot.description:
        sections.append(f"## 页面描述\n\n{snapshot.description.strip()}")
    if snapshot.metadata:
        sections.append("## 元数据\n\n" + "\n".join(f"- {key}: {value}" for key, value in snapshot.metadata))
    if snapshot.headings:
        sections.append("## 页面标题结构\n\n" + "\n".join(f"- {heading}" for heading in snapshot.headings))
    if snapshot.controls:
        sections.append("## 可交互控件\n\n" + "\n".join(f"- {control}" for control in snapshot.controls))
    if snapshot.text_blocks:
        sections.append("## 可见文本\n\n" + "\n".join(f"- {text}" for text in snapshot.text_blocks))
    if snapshot.lists:
        list_blocks = []
        for index, items in enumerate(snapshot.lists, start=1):
            list_blocks.append(f"### 列表 {index}\n\n" + "\n".join(f"- {item}" for item in items))
        sections.append("## 列表\n\n" + "\n\n".join(list_blocks))
    if snapshot.tables:
        table_blocks = []
        for index, table in enumerate(snapshot.tables, start=1):
            table_blocks.append(f"### 表格 {index}\n\n{markdown_table(table['rows'])}")
        sections.append("## 表格\n\n" + "\n\n".join(table_blocks))
    if snapshot.links:
        sections.append("## 主要链接\n\n" + "\n".join(f"- [{text}]({href})" for text, href in snapshot.links))
    if snapshot.images:
        sections.append("## 图片\n\n" + "\n".join(f"- [{label}]({src})" for label, src in snapshot.images))
    if snapshot.media:
        sections.append("## 媒体\n\n" + "\n".join(f"- [{label}]({src})" for label, src in snapshot.media))
    if not sections:
        sections.append("## 页面快照\n\n未检测到可提取的可见正文内容。")
    return "\n\n".join(sections)


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    separator = ["---"] * width
    body = normalized[1:]
    lines = [
        "| " + " | ".join(escape_table_cell(cell) for cell in header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(escape_table_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


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
