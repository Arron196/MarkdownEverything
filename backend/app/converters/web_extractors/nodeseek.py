from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.converters.web_extractors.base import WebExtractorContext, WebExtractorResult
from app.converters.web_extractors.utils import clean_markdown, markdown_from_node, normalize_links, normalize_text


@dataclass
class NodeSeekPost:
    floor: str
    author: str | None
    author_url: str | None
    role: str | None
    published_at: str | None
    category: str | None
    url: str
    markdown: str


def extract(context: WebExtractorContext) -> WebExtractorResult | None:
    if not is_nodeseek_post(context.base_url):
        return None
    body = nodeseek_post_markdown(context.soup, context.base_url, context.title)
    if not body:
        return None
    return WebExtractorResult(
        name="nodeseek-post",
        body=body,
        score=910,
        metadata={"post_count": body.count("\n## #")},
    )


def is_nodeseek_post(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    return (host == "nodeseek.com" or host.endswith(".nodeseek.com")) and parsed.path.startswith("/post-")


def nodeseek_post_markdown(soup: BeautifulSoup, base_url: str, title: str) -> str | None:
    posts = extract_nodeseek_posts(soup, base_url)
    if not posts:
        return None

    topic_info = nodeseek_topic_info(soup, base_url, title, posts[0])
    sections = []
    if topic_info:
        sections.append("## 主题信息\n\n" + "\n".join(topic_info))
    sections.append(
        "## 楼层目录\n\n"
        + "\n".join(
            f"- #{post.floor} {post.author or 'Unknown'}"
            + (f" · {post.published_at}" if post.published_at else "")
            + (f" · {post.url}" if post.url else "")
            for post in posts
        )
    )
    for post in posts:
        heading = f"## #{post.floor} {post.author or 'Unknown'}"
        metadata = []
        if post.role:
            metadata.append(f"- 角色：{post.role}")
        if post.published_at:
            metadata.append(f"- 发布时间：{post.published_at}")
        if post.category:
            metadata.append(f"- 版块：{post.category}")
        if post.author_url:
            metadata.append(f"- 作者主页：{post.author_url}")
        if post.url:
            metadata.append(f"- 楼层链接：{post.url}")
        sections.append(f"{heading}\n\n{chr(10).join(metadata)}\n\n{post.markdown}".strip())
    return "\n\n".join(sections).strip()


def extract_nodeseek_posts(soup: BeautifulSoup, base_url: str) -> list[NodeSeekPost]:
    posts: list[NodeSeekPost] = []
    seen: set[tuple[str, str, str]] = set()
    for item in soup.select(".content-item"):
        content = item.select_one("article.post-content")
        if not content:
            continue
        content_copy = deepcopy(content)
        clean_nodeseek_content(content_copy)
        normalize_links(content_copy, base_url)
        normalize_embedded_media(content_copy, base_url)
        markdown = clean_markdown(markdown_from_node(content_copy), "")
        if not markdown:
            continue

        floor = nodeseek_floor(item)
        author, author_url = nodeseek_author(item, base_url)
        key = (floor, author or "", markdown)
        if key in seen:
            continue
        seen.add(key)
        posts.append(
            NodeSeekPost(
                floor=floor,
                author=author,
                author_url=author_url,
                role=nodeseek_role(item),
                published_at=nodeseek_published_at(item),
                category=nodeseek_category(item),
                url=nodeseek_floor_url(base_url, floor),
                markdown=markdown,
            )
        )
    posts.sort(key=lambda post: int(post.floor) if post.floor.isdigit() else 10**9)
    return posts


def clean_nodeseek_content(node) -> None:
    for tag in list(node.select("script, style, button, svg, .comment-menu, .reward-container")):
        if tag.parent is not None:
            tag.decompose()
    for tag in node.find_all(True):
        tag.attrs = {
            key: value
            for key, value in tag.attrs.items()
            if key in {"href", "src", "alt", "title"}
        }


def normalize_embedded_media(node, base_url: str) -> None:
    for tag in node.find_all(["img", "video", "audio", "source"]):
        for attr in ["src", "data-src", "poster"]:
            value = tag.get(attr)
            if isinstance(value, str) and value.strip() and not value.startswith(("data:", "blob:")):
                tag[attr] = urljoin(base_url, value.strip())


def nodeseek_topic_info(soup: BeautifulSoup, base_url: str, title: str, first_post: NodeSeekPost) -> list[str]:
    info = [f"- 标题：{nodeseek_title(soup, title)}", f"- 原始链接：{base_url}"]
    if first_post.author:
        info.append(f"- 楼主：{first_post.author}")
    if first_post.category:
        info.append(f"- 版块：{first_post.category}")
    if first_post.published_at:
        info.append(f"- 发布时间：{first_post.published_at}")
    return info


def nodeseek_title(soup: BeautifulSoup, fallback_title: str) -> str:
    for selector in [".post-title-link", ".post-title", "h1"]:
        node = soup.select_one(selector)
        if node:
            text = normalize_text(node.get_text(" ", strip=True))
            if text:
                return text
    return normalize_text(fallback_title) or "Untitled"


def nodeseek_floor(item) -> str:
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id.strip():
        return item_id.strip().removeprefix("#")
    floor_link = item.select_one(".floor-link")
    if floor_link:
        text = normalize_text(floor_link.get_text(" ", strip=True))
        if text:
            return text.removeprefix("#")
    return str(0)


def nodeseek_author(item, base_url: str) -> tuple[str | None, str | None]:
    node = item.select_one(".author-name")
    if not node:
        return None, None
    author = normalize_text(node.get_text(" ", strip=True)) or None
    href = node.get("href")
    author_url = urljoin(base_url, href) if isinstance(href, str) and href.strip() else None
    return author, author_url


def nodeseek_role(item) -> str | None:
    node = item.select_one(".is-poster, .role-tag")
    if not node:
        return None
    return normalize_text(node.get_text(" ", strip=True)) or None


def nodeseek_published_at(item) -> str | None:
    node = item.select_one(".content-info")
    if not node:
        return None
    text = normalize_text(node.get_text(" ", strip=True))
    text = text.split(" in ", 1)[0].strip()
    return text or None


def nodeseek_category(item) -> str | None:
    node = item.select_one(".content-category a, .content-category")
    if not node:
        return None
    text = normalize_text(node.get_text(" ", strip=True))
    return text.removeprefix("in ").strip() or None


def nodeseek_floor_url(base_url: str, floor: str) -> str:
    parsed = urlparse(base_url)
    root = parsed._replace(fragment="", query="").geturl()
    return f"{root}#{floor}" if floor else root
