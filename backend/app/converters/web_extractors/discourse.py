from copy import deepcopy
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.converters.web_extractors.base import WebExtractorContext, WebExtractorResult
from app.converters.web_extractors.utils import (
    clean_markdown,
    markdown_from_node,
    normalize_links,
    normalize_text,
    unique_preserve_order,
    visible_text,
)


def extract(context: WebExtractorContext) -> WebExtractorResult | None:
    body = discourse_topic_markdown(context.soup, context.base_url, context.title)
    if not body:
        return None
    return WebExtractorResult(
        name="discourse-topic",
        body=body,
        score=900 if context.rendered else 700,
        metadata={"post_count": body.count("\n## #")},
    )


def discourse_topic_markdown(soup: BeautifulSoup, base_url: str, title: str) -> str | None:
    posts = []
    for index, post in enumerate(soup.select("[data-post-id]"), start=1):
        cooked = post.select_one(".cooked")
        if not cooked or len(visible_text(cooked)) < 2:
            continue
        cooked_copy = deepcopy(cooked)
        clean_discourse_post(cooked_copy)
        normalize_links(cooked_copy, base_url)
        post_markdown = clean_markdown(markdown_from_node(cooked_copy), "")
        if not post_markdown:
            continue
        author = discourse_post_author(post)
        published_at = discourse_post_date(post)
        post_url = discourse_post_url(post, base_url, index)
        posts.append(
            {
                "index": index,
                "author": author,
                "published_at": published_at,
                "url": post_url,
                "markdown": post_markdown,
            }
        )

    if not posts:
        return None

    category_tags = discourse_topic_tags(soup)
    sections = []
    if category_tags:
        sections.append("## 主题信息\n\n" + "\n".join(f"- {item}" for item in category_tags))
    sections.append(
        "## 帖子目录\n\n"
        + "\n".join(
            f"- #{post['index']} {post['author'] or 'Unknown'}"
            + (f" · {post['published_at']}" if post["published_at"] else "")
            + (f" · {post['url']}" if post["url"] else "")
            for post in posts
        )
    )
    for post in posts:
        heading = f"## #{post['index']} {post['author'] or 'Unknown'}"
        meta = []
        if post["published_at"]:
            meta.append(f"- 发布时间：{post['published_at']}")
        if post["url"]:
            meta.append(f"- 原帖链接：{post['url']}")
        meta_markdown = "\n".join(meta)
        sections.append(f"{heading}\n\n{meta_markdown}\n\n{post['markdown']}".strip())
    return "\n\n".join(sections).strip()


def clean_discourse_post(node) -> None:
    for tag in list(
        node.select(
            "script, style, button, svg, .codeblock-button-wrapper, .cooked-selection-barrier, "
            ".lightbox-wrapper, .meta, .post-menu-area, .topic-map"
        )
    ):
        if tag.parent is not None:
            tag.decompose()
    for tag in node.find_all(True):
        if tag.attrs is None:
            continue
        tag.attrs = {
            key: value
            for key, value in tag.attrs.items()
            if key in {"href", "src", "alt", "title"}
        }


def discourse_post_author(post) -> str | None:
    for selector in [".names .username a", ".username a", ".names .first a", "[data-user-card]"]:
        node = post.select_one(selector)
        if node:
            text = normalize_text(node.get_text(" ", strip=True))
            if text:
                return text
    return None


def discourse_post_date(post) -> str | None:
    node = post.select_one(".relative-date")
    if not node:
        return None
    title = node.get("title")
    if isinstance(title, str) and title.strip():
        return normalize_text(title)
    return normalize_text(node.get_text(" ", strip=True)) or None


def discourse_post_url(post, base_url: str, index: int) -> str | None:
    node = post.select_one(".post-date[href], .post-date a[href], .relative-date")
    href = None
    if node:
        href = node.get("href")
        if not href:
            parent_link = node.find_parent("a")
            href = parent_link.get("href") if parent_link else None
    if isinstance(href, str) and href.strip():
        return urljoin(base_url, href)
    parsed = urlparse(base_url)
    if parsed.scheme and parsed.netloc and index > 1:
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}/{index}"
    return base_url


def discourse_topic_tags(soup: BeautifulSoup) -> list[str]:
    items: list[str] = []
    category = soup.select_one(".badge-category__name, .category-name")
    if category:
        text = normalize_text(category.get_text(" ", strip=True))
        if text:
            items.append(f"分类：{text}")
    tags = unique_preserve_order(
        normalize_text(tag.get_text(" ", strip=True))
        for tag in soup.select(".discourse-tag, .tag-entity")
        if normalize_text(tag.get_text(" ", strip=True))
    )
    if tags:
        items.append("标签：" + "、".join(tags[:12]))
    return items
