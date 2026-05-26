import mimetypes
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup, Comment
from fastapi import HTTPException, status
from markdownify import markdownify as md

from app.config import settings
from app.converters.base import ConversionResult
from app.services.url_security import assert_public_url

try:
    from readability import Document as ReadabilityDocument
except Exception:  # pragma: no cover - optional dependency in local dev environments.
    ReadabilityDocument = None

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover - optional dependency in local dev environments.
    PlaywrightTimeoutError = None
    async_playwright = None


FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 MarkdownEverything/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

MIN_MEANINGFUL_TEXT_LENGTH = 200
MAX_IMAGE_BYTES = 10 * 1024 * 1024
STATIC_EXTRACTION_MIN_LENGTH = 120

CONTENT_SELECTORS: list[tuple[str, float]] = [
    ("#content", 60),
    ("main article", 58),
    ("[role='main'] article", 56),
    ("article", 52),
    (".markdown-body", 50),
    (".mdx-content", 50),
    ("main .prose", 48),
    (".prose", 44),
    (".docs-content", 42),
    (".doc-content", 42),
    (".documentation", 38),
    ("[data-pagefind-body]", 38),
    ("[role='main']", 34),
    ("main", 30),
    (".content", 12),
]

NOISE_SELECTORS = (
    "script, style, noscript, template, nav, footer, aside, form, button, input, select, textarea, "
    "iframe, object, embed, canvas, svg, dialog, "
    "[hidden], [aria-hidden='true'], [style*='display:none'], [style*='display: none'], "
    "[style*='visibility:hidden'], [style*='visibility: hidden'], [data-agent-docs-index], "
    "[role='navigation'], [role='search'], [role='complementary'], [role='banner'], "
    "[aria-label='导航到标题'], [aria-label='Navigate to heading'], "
    "#table-of-contents-content, #toc, .toc, .table-of-contents, .sidebar, .breadcrumbs, "
    ".breadcrumb, .sr-only, .visually-hidden, .screen-reader-text, "
    "[class*='TableOfContents'], [class*='table-of-contents'], [class*='toc'], [id*='toc'], "
    "[class*='sidebar'], [id*='sidebar'], [class*='breadcrumb'], [id*='breadcrumb'], "
    "[class*='navbar'], [class*='navigation'], [id*='navigation'], "
    "[class*='pagination'], [class*='pager'], [class*='share'], [class*='social'], "
    "[class*='advert'], [class*='cookie'], [class*='newsletter'], [class*='subscribe']"
)

NOISE_TEXT_PATTERNS = [
    re.compile(r"^skip to main content$", re.I),
    re.compile(r"^跳转到主要内容$"),
    re.compile(r"^(open in chatgpt|openai\s+在\s+chatgpt\s+中打开)$", re.I),
]

NAV_PENALTY_TERMS = [
    "navigation",
    "table of contents",
    "on this page",
    "在此页面",
    "目录",
    "搜索",
    "search",
    "subscribe",
    "newsletter",
    "cookie",
    "广告",
]

IMAGE_SOURCE_ATTRS = [
    "src",
    "data-src",
    "data-original",
    "data-lazy-src",
    "data-image",
    "data-url",
]

IMAGE_SRCSET_ATTRS = ["srcset", "data-srcset"]


@dataclass
class ContentCandidate:
    name: str
    node: BeautifulSoup
    score: float
    text_length: int


@dataclass
class RenderedPage:
    html: str
    title: str | None
    final_url: str


@dataclass
class PageSnapshot:
    title: str
    description: str | None
    headings: list[str]
    text_blocks: list[str]
    controls: list[str]
    links: list[tuple[str, str]]
    images: list[tuple[str, str]]


async def convert_webpage(url: str, assets_dir: Path) -> ConversionResult:
    rendered_page: RenderedPage | None = None
    try:
        html, final_url = await fetch_html(url)
    except httpx.HTTPStatusError as exc:
        if not should_render_after_fetch_error(exc):
            raise
        rendered_page = await render_page(url)
        if not rendered_page:
            raise
        html = rendered_page.html
        final_url = rendered_page.final_url

    soup = BeautifulSoup(html, "html.parser")
    title = extract_title(soup, final_url, rendered_page.title if rendered_page else None)
    author = extract_author(soup)
    created_at = extract_created_at(soup)

    candidate = select_content_candidate(html, soup, final_url)

    if rendered_page is None and should_render_fallback(candidate):
        rendered_page = await render_page(final_url)
        if rendered_page:
            rendered_soup = BeautifulSoup(rendered_page.html, "html.parser")
            rendered_candidate = select_content_candidate(rendered_page.html, rendered_soup, rendered_page.final_url)
            if rendered_candidate.score > candidate.score or rendered_candidate.text_length > candidate.text_length:
                html = rendered_page.html
                soup = rendered_soup
                final_url = rendered_page.final_url
                candidate = rendered_candidate
                title = extract_title(soup, final_url, rendered_page.title)
                author = extract_author(soup) or author
                created_at = extract_created_at(soup) or created_at

    content = candidate.node
    normalize_links(content, final_url)
    resources = await download_images(content, final_url, assets_dir)

    body = markdown_from_node(content)
    if not meaningful_body(body):
        extracted = trafilatura.extract(
            html,
            url=final_url,
            include_links=True,
            include_tables=True,
            include_images=True,
            include_formatting=True,
            output_format="markdown",
            favor_recall=True,
        )
        body = extracted or body

    body = clean_markdown(body, title)
    extractor_name = candidate.name
    discourse_body = discourse_topic_markdown(soup, final_url, title)
    if discourse_body and (rendered_page is not None or len(discourse_body) > len(body)):
        body = discourse_body
        extractor_name = "discourse-topic"

    if discourse_body is None and (
        not meaningful_body(body) or should_use_snapshot_body(candidate, body, rendered_page is not None)
    ):
        snapshot = build_page_snapshot(soup, final_url, title)
        body = render_page_snapshot_markdown(snapshot)
        extractor_name = "rendered-snapshot" if rendered_page else "static-snapshot"

    return ConversionResult(
        title=title.strip(),
        source_type="webpage",
        body=body.strip(),
        summary_seed=body,
        source_url=final_url,
        author=author,
        created_at=created_at,
        resources=resources,
        metadata={"extractor": extractor_name, "extractor_score": round(candidate.score, 2)},
    )


async def fetch_html(url: str) -> tuple[str, str]:
    current_url = url
    limits = httpx.Limits(max_connections=4)
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        follow_redirects=False,
        limits=limits,
        headers=FETCH_HEADERS,
    ) as client:
        for _ in range(settings.redirect_limit + 1):
            assert_public_url(current_url)
            response = await client.get(current_url)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Redirect location is empty")
                current_url = urljoin(str(response.url), location)
                continue

            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL does not look like an HTML page")
            if len(response.content) > settings.max_web_response_bytes:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Web response is too large")
            return response.text, str(response.url)

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Too many redirects")


def metadata_value(soup: BeautifulSoup, key: str) -> str | None:
    tag = soup.find("meta", attrs={"name": key}) or soup.find("meta", attrs={"property": key})
    if not tag:
        return None
    content = tag.get("content")
    return content.strip() if isinstance(content, str) else None


def extract_title(soup: BeautifulSoup, url: str, rendered_title: str | None = None) -> str:
    page_title_value = rendered_title or page_title(soup)
    social_title = metadata_value(soup, "og:title") or metadata_value(soup, "twitter:title")
    title = best_title(page_title_value, social_title) or urlparse(url).netloc
    return clean_title(title)


def extract_author(soup: BeautifulSoup) -> str | None:
    return (
        metadata_value(soup, "author")
        or metadata_value(soup, "article:author")
        or metadata_value(soup, "twitter:creator")
        or metadata_value(soup, "parsely-author")
    )


def extract_created_at(soup: BeautifulSoup) -> str | None:
    for key in [
        "article:published_time",
        "date",
        "datePublished",
        "publishdate",
        "pubdate",
        "parsely-pub-date",
    ]:
        value = metadata_value(soup, key)
        if value:
            return value
    time_tag = soup.find("time")
    if time_tag:
        datetime_value = time_tag.get("datetime")
        if isinstance(datetime_value, str) and datetime_value.strip():
            return datetime_value.strip()
    return None


def page_title(soup: BeautifulSoup) -> str | None:
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(" ", strip=True)
    return soup.title.get_text(" ", strip=True) if soup.title else None


def clean_title(title: str) -> str:
    title = normalize_text(title)
    title = re.sub(r"\s+[-|·]\s+.*$", "", title)
    return title.strip() or "Untitled"


def best_title(page_title_value: str | None, social_title: str | None) -> str | None:
    page_title_value = normalize_text(page_title_value or "")
    social_title = normalize_text(social_title or "")
    if not social_title:
        return page_title_value or None
    if not page_title_value:
        return social_title
    if re.search(r"(搜索|search|home|homepage|首页)", page_title_value, re.I):
        return page_title_value
    if len(social_title) > len(page_title_value) + 20 and re.search(r"[。.!?]", social_title):
        return page_title_value
    return social_title


def should_render_fallback(candidate: ContentCandidate) -> bool:
    if not settings.web_render_enabled or async_playwright is None:
        return False
    if candidate.text_length < STATIC_EXTRACTION_MIN_LENGTH:
        return True
    if candidate.name == "body-fallback" and candidate.score <= 0:
        return True
    return False


def should_use_snapshot_body(candidate: ContentCandidate, body: str, was_rendered: bool) -> bool:
    if candidate.name in {"body-fallback"}:
        return True
    if not was_rendered:
        return False
    if candidate.score < 80 and len(body) < 3000:
        return True
    if candidate.name in {"trafilatura", "readability"} and len(re.findall(r"(?m)^#{1,6}\s+", body)) == 0:
        return True
    return False


def should_render_after_fetch_error(exc: httpx.HTTPStatusError) -> bool:
    if not settings.web_render_enabled or async_playwright is None:
        return False
    return exc.response.status_code in {401, 403, 406, 409, 429, 503}


async def render_page(url: str) -> RenderedPage | None:
    if async_playwright is None:
        return None
    assert_public_url(url)
    timeout_ms = int(settings.web_render_timeout_seconds * 1000)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ],
            )
            context = await browser.new_context(
                user_agent=FETCH_HEADERS["User-Agent"],
                locale="zh-CN",
                viewport={"width": 1366, "height": 900},
                ignore_https_errors=False,
            )
            page = await context.new_page()

            async def guard_request(route):
                request_url = route.request.url
                resource_type = route.request.resource_type
                if resource_type in {"media", "font"}:
                    await route.abort()
                    return
                try:
                    assert_public_url(request_url)
                except Exception:
                    await route.abort()
                    return
                await route.continue_()

            await page.route("**/*", guard_request)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if response and response.status >= 400:
                await context.close()
                await browser.close()
                return None
            try:
                await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
            except Exception:
                pass
            await page.wait_for_timeout(settings.web_render_wait_ms)
            final_url = page.url
            assert_public_url(final_url)
            title = await page.title()
            html = await page.content()
            await context.close()
            await browser.close()
            return RenderedPage(html=html, title=title, final_url=final_url)
    except Exception:
        return None


def select_content_candidate(html: str, soup: BeautifulSoup, base_url: str | None = None) -> ContentCandidate:
    candidates: list[ContentCandidate] = []
    seen: set[int] = set()

    for selector, bonus in CONTENT_SELECTORS:
        for node in soup.select(selector)[:8]:
            node_id = id(node)
            if node_id in seen:
                continue
            seen.add(node_id)
            candidate = build_candidate(selector, node, bonus)
            if candidate:
                candidates.append(candidate)

    candidates.extend(extractor_candidates(html, base_url))
    candidates.extend(heuristic_candidates(soup))

    if candidates:
        return max(candidates, key=lambda candidate: candidate.score)

    body = deepcopy(soup.body or soup)
    clean_content_node(body)
    return ContentCandidate(name="body-fallback", node=body, score=0, text_length=len(visible_text(body)))


def select_content_node(soup: BeautifulSoup):
    return select_content_candidate(str(soup), soup).node


def extractor_candidates(html: str, base_url: str | None) -> list[ContentCandidate]:
    candidates: list[ContentCandidate] = []
    extracted = trafilatura.extract(
        html,
        url=base_url,
        include_links=True,
        include_tables=True,
        include_images=True,
        include_formatting=True,
        output_format="html",
        favor_recall=True,
    )
    if extracted:
        node = BeautifulSoup(extracted, "html.parser")
        candidate = build_candidate("trafilatura", node, 30)
        if candidate:
            candidates.append(candidate)

    if ReadabilityDocument is not None:
        try:
            document = ReadabilityDocument(html)
            summary = document.summary(html_partial=True)
            if summary:
                node = BeautifulSoup(summary, "html.parser")
                candidate = build_candidate("readability", node, 28)
                if candidate:
                    candidates.append(candidate)
        except Exception:
            pass

    return candidates


def heuristic_candidates(soup: BeautifulSoup) -> list[ContentCandidate]:
    body = soup.body or soup
    candidates: list[ContentCandidate] = []
    for index, node in enumerate(body.find_all(["article", "main", "section", "div"], recursive=True)[:400]):
        if len(visible_text(node)) < MIN_MEANINGFUL_TEXT_LENGTH:
            continue
        if is_noisy_node(node):
            continue
        candidate = build_candidate(f"heuristic:{node.name}:{index}", node, -5)
        if candidate:
            candidates.append(candidate)
    return candidates


def build_candidate(name: str, node, selector_bonus: float) -> ContentCandidate | None:
    cloned = deepcopy(node)
    clean_content_node(cloned)
    text = visible_text(cloned)
    if len(text) < 80:
        return None
    score = score_content(cloned, text) + selector_bonus
    return ContentCandidate(name=name, node=cloned, score=score, text_length=len(text))


def clean_content_node(node) -> None:
    for comment in node.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()

    for tag in list(node.select(NOISE_SELECTORS)):
        if tag.parent is None:
            continue
        tag.decompose()

    for tag in list(node.find_all(["blockquote", "section", "div", "p", "a", "button"])):
        if tag.parent is None:
            continue
        text = visible_text(tag)
        if is_agent_docs_block(text):
            tag.decompose()
            continue
        if len(text) <= 80 and any(pattern.search(text) for pattern in NOISE_TEXT_PATTERNS):
            tag.decompose()

    for anchor in list(node.find_all("a")):
        if anchor.parent is None:
            continue
        href = anchor.get("href", "")
        text = anchor.get_text(" ", strip=True)
        if isinstance(href, str) and href.startswith("#") and not text:
            anchor.decompose()
        elif not text and not anchor.find("img"):
            anchor.decompose()

    for tag in list(node.find_all(True)):
        if tag.parent is None or tag.attrs is None:
            continue
        if tag.name in {"p", "li", "span", "div"} and not visible_text(tag) and not tag.find(["img", "pre", "code", "table"]):
            tag.decompose()
            continue
        allowed_attrs = {"href", "src", "alt", "title", "datetime", "srcset", "width", "height", *IMAGE_SOURCE_ATTRS, *IMAGE_SRCSET_ATTRS}
        tag.attrs = {key: value for key, value in tag.attrs.items() if key in allowed_attrs}


def score_content(node, text: str) -> float:
    text_length = len(text)
    paragraphs = count_text_tags(node, ["p"], min_length=40)
    list_items = count_text_tags(node, ["li"], min_length=25)
    headings = len(node.find_all(re.compile(r"^h[1-6]$")))
    pre_lengths = [len(pre.get_text("", strip=True)) for pre in node.find_all("pre")]
    substantial_pre_blocks = len([length for length in pre_lengths if length >= 24])
    small_pre_blocks = len(pre_lengths) - substantial_pre_blocks
    inline_code_chars = sum(
        len(code.get_text("", strip=True))
        for code in node.find_all("code")
        if code.find_parent("pre") is None
    )
    tables = len(node.find_all("table"))
    images = len(node.find_all("img"))
    link_text_length = sum(len(visible_text(anchor)) for anchor in node.find_all("a"))
    link_density = link_text_length / max(text_length, 1)
    nav_hits = sum(text.lower().count(term.lower()) for term in NAV_PENALTY_TERMS)

    score = min(text_length, 30000) / 75
    score += min(paragraphs, 80) * 3.2
    score += min(list_items, 100) * 1.1
    score += min(headings, 40) * 8
    score += min(substantial_pre_blocks, 25) * 18
    score += min(small_pre_blocks, 20) * 1.5
    score += min(inline_code_chars, 2000) / 80
    score += min(tables, 15) * 18
    score += min(images, 30) * 2
    score -= min(link_density, 1) * 260
    score -= nav_hits * 8
    if is_agent_docs_block(text):
        score -= 260

    if text_length < MIN_MEANINGFUL_TEXT_LENGTH:
        score -= 35 if headings >= 2 or substantial_pre_blocks or tables else 180
    if link_density > 0.55 and paragraphs < 4:
        score -= 220
    if headings == 0 and paragraphs < 3 and text_length < 900:
        score -= 120
    return score


def count_text_tags(node, names: list[str], min_length: int) -> int:
    return sum(1 for tag in node.find_all(names) if len(visible_text(tag)) >= min_length)


def is_noisy_node(node) -> bool:
    attrs = " ".join(
        str(value).lower()
        for key, value in node.attrs.items()
        if key in {"id", "class", "role", "aria-label"}
    )
    return bool(re.search(r"(nav|sidebar|toc|breadcrumb|footer|header|menu|share|social|cookie|advert|newsletter)", attrs))


def is_agent_docs_block(text: str) -> bool:
    lowered = text.lower()
    return "documentation index" in lowered and "llms.txt" in lowered


def normalize_links(node, base_url: str) -> None:
    for anchor in list(node.find_all("a")):
        href = anchor.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        href = href.strip()
        parsed = urlparse(href)
        if href.startswith("#") or parsed.scheme in {"mailto", "tel"}:
            anchor["href"] = href
            continue
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            anchor.unwrap()
            continue
        anchor["href"] = urljoin(base_url, href)


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
        label = normalize_text(img.get("alt") or img.get("title") or Path(urlparse(image_url).path).name or "image")
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


def unique_preserve_order(values) -> list:
    result = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def markdown_from_node(node) -> str:
    return md(
        str(node),
        heading_style="ATX",
        bullets="-",
        strip=["script", "style"],
    )


def meaningful_body(body: str) -> bool:
    text = re.sub(r"[\s#`|\\-]+", "", body)
    return len(text) > MIN_MEANINGFUL_TEXT_LENGTH


def clean_markdown(body: str, title: str) -> str:
    text = body.replace("\u200b", "").replace("\xa0", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"(?m)^(#{1,6})\s*\n+([^#`\n][^\n]{0,140})\s*$", r"\1 \2", text)
    text = re.sub(
        r"(?is)(?:^|\n)>?\s*#{1,6}\s*Documentation Index\b.{0,1200}?Use this file to discover all available pages before exploring further\.?",
        "\n",
        text,
    )
    text = re.sub(
        r"(?im)^\s*(跳转到主要内容|Skip to main content|OpenAI 在 ChatGPT 中打开|Open in ChatGPT)\s*$\n?",
        "",
        text,
    )
    text = remove_duplicate_title(text, title)
    text = re.sub(r"(?m)^\s*#{1,6}\s*$\n?", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\n(#{2,6} )", r"\n\n\1", text)
    text = re.sub(r"\n(```)", r"\n\n\1", text)
    text = re.sub(r"```\n{2,}", "```\n", text)
    text = re.sub(r"\n{2,}```", "\n\n```", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def remove_duplicate_title(text: str, title: str) -> str:
    if not title.strip():
        return text
    title_key = comparable_heading(title)
    lines = text.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        return text

    first = lines[index].strip()
    heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", first)
    first_text = heading_match.group(1) if heading_match else first
    if comparable_heading(first_text) != title_key:
        return text

    del lines[index]
    while index < len(lines) and not lines[index].strip():
        del lines[index]
    return "\n".join(lines)


def comparable_heading(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()


def visible_text(node) -> str:
    return normalize_text(node.get_text(" ", strip=True))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


async def download_images(soup: BeautifulSoup, base_url: str, assets_dir: Path) -> list[str]:
    resources: list[str] = []
    downloaded_by_url: dict[str, str] = {}
    downloaded = 0
    assets_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        follow_redirects=True,
        max_redirects=2,
        headers=FETCH_HEADERS,
    ) as client:
        for img in soup.find_all("img"):
            if downloaded >= settings.max_images_per_job:
                break
            image_url = extract_image_source(img, base_url)
            if not image_url or is_placeholder_image(img, image_url):
                img.decompose()
                continue

            if image_url in downloaded_by_url:
                img["src"] = downloaded_by_url[image_url]
                continue

            try:
                assert_public_url(image_url)
                response = await client.get(image_url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if not content_type.startswith("image/") or len(response.content) > MAX_IMAGE_BYTES:
                    img["src"] = image_url
                    continue

                extension = image_extension(content_type, image_url)
                downloaded += 1
                filename = f"image-{downloaded}{extension}"
                local_src = f"./assets/{filename}"
                (assets_dir / filename).write_bytes(response.content)
                img["src"] = local_src
                downloaded_by_url[image_url] = local_src
                alt_text = normalize_text(img.get("alt", ""))
                suffix = f"（{alt_text}）" if alt_text else ""
                resources.append(f"- 图片资源：{local_src}{suffix}")
            except Exception:
                img["src"] = image_url
                continue

    return resources


def extract_image_source(img, base_url: str) -> str | None:
    candidates: list[str] = []
    for attr in IMAGE_SOURCE_ATTRS:
        value = img.get(attr)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    for attr in IMAGE_SRCSET_ATTRS:
        value = img.get(attr)
        if isinstance(value, str) and value.strip():
            srcset_url = best_srcset_url(value)
            if srcset_url:
                candidates.append(srcset_url)

    for candidate in candidates:
        if candidate.startswith(("data:", "blob:", "javascript:")):
            continue
        absolute = urljoin(base_url, candidate)
        if urlparse(absolute).scheme in {"http", "https"}:
            return absolute
    return None


def best_srcset_url(srcset: str) -> str | None:
    best_url: str | None = None
    best_score = -1.0
    for raw_entry in srcset.split(","):
        parts = raw_entry.strip().split()
        if not parts:
            continue
        url = parts[0]
        descriptor = parts[1] if len(parts) > 1 else ""
        score = 1.0
        if descriptor.endswith("w"):
            score = float(descriptor[:-1] or 0)
        elif descriptor.endswith("x"):
            score = float(descriptor[:-1] or 1) * 1000
        if score > best_score:
            best_url = url
            best_score = score
    return best_url


def is_placeholder_image(img, image_url: str) -> bool:
    path = urlparse(image_url).path.lower()
    if re.search(r"(spacer|pixel|tracking|transparent|blank|placeholder)", path):
        return True
    try:
        width = int(str(img.get("width", "0")).replace("px", "") or 0)
        height = int(str(img.get("height", "0")).replace("px", "") or 0)
        return 0 < width <= 2 and 0 < height <= 2
    except ValueError:
        return False


def image_extension(content_type: str, image_url: str) -> str:
    extension = mimetypes.guess_extension(content_type) or Path(urlparse(image_url).path).suffix or ".img"
    if extension in {".jpe", ".jpeg"}:
        return ".jpg"
    return extension
