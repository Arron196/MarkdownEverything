import asyncio
import mimetypes
import re
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup, Comment
from fastapi import HTTPException, status

from app.config import settings
from app.converters.base import ConversionResult
from app.converters.web_extractors import SNAPSHOT_EXTRACTOR, WebExtractorContext, run_specialized_extractors
from app.converters.web_extractors.discourse import discourse_topic_markdown
from app.converters.web_extractors.snapshot import build_page_snapshot, render_page_snapshot_markdown
from app.converters.web_extractors.utils import (
    clean_markdown,
    extract_image_source,
    is_placeholder_image,
    markdown_from_node,
    normalize_links,
    normalize_text,
    visible_text,
)
from app.converters.web_engine.analysis import analyze_page
from app.converters.web_engine.candidates import (
    generate_candidates as engine_generate_candidates,
    snapshot_candidate as engine_snapshot_candidate,
    specialized_candidate as engine_specialized_candidate,
)
from app.converters.web_engine.decision import (
    select_winner as engine_select_winner,
    should_render_static_page,
    top_candidate_metadata,
)
from app.converters.web_engine.models import ExtractionCandidate
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


async def convert_webpage(url: str, assets_dir: Path) -> ConversionResult:
    rendered_page: RenderedPage | None = None
    rendered_candidates_added = False
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
    except httpx.RequestError as exc:
        if not should_render_after_request_error(exc):
            raise
        rendered_page = await render_page(url)
        if not rendered_page:
            raise
        html = rendered_page.html
        final_url = rendered_page.final_url

    soup = BeautifulSoup(html, "html.parser")
    analysis = analyze_page(soup, final_url, rendered_page.title if rendered_page else None)
    candidates = collect_engine_candidates(html, soup, analysis, rendered=rendered_page is not None)
    maybe_add_snapshot_candidate(candidates, soup, analysis, rendered=rendered_page is not None)
    best_static = engine_select_winner(candidates, analysis)

    rendered_soup: BeautifulSoup | None = soup if rendered_page else None
    rendered_analysis = analysis if rendered_page else None
    if (
        rendered_page is None
        and not analysis.blocked_reason
        and should_render_static_page(best_static, analysis, async_playwright is not None)
    ):
        rendered_page = await render_page(final_url)
        if rendered_page:
            rendered_candidates_added = True
            rendered_soup = BeautifulSoup(rendered_page.html, "html.parser")
            rendered_analysis = analyze_page(rendered_soup, rendered_page.final_url, rendered_page.title)
            rendered_candidates = collect_engine_candidates(
                rendered_page.html,
                rendered_soup,
                rendered_analysis,
                rendered=True,
            )
            maybe_add_snapshot_candidate(rendered_candidates, rendered_soup, rendered_analysis, rendered=True)
            candidates.extend(rendered_candidates)

    winner = engine_select_winner(candidates, rendered_analysis or analysis)
    if winner is None:
        raise ValueError("Source page did not contain extractable public content")

    winner_base_url = str(winner.metadata.get("base_url") or final_url)
    winner_soup = rendered_soup if rendered_page and winner_base_url == rendered_page.final_url else soup
    winner_analysis = rendered_analysis if rendered_page and winner_base_url == rendered_page.final_url else analysis
    title = (winner.metadata.get("title") or winner_analysis.title or extract_title(winner_soup, winner_base_url)).strip()
    author = extract_author(winner_soup)
    created_at = extract_created_at(winner_soup)

    if winner_analysis.blocked_reason:
        raise ValueError("Source site returned an access restriction or anti-bot challenge instead of public page content")

    resources: list[str] = []
    if winner.node is not None:
        resources = await download_images(winner.node, winner_base_url, assets_dir)
        body = clean_markdown(markdown_from_node(winner.node), title)
    else:
        body = clean_markdown(winner.markdown, title)

    restriction_message = access_restriction_message(body, title)
    if restriction_message:
        raise ValueError(restriction_message)

    return ConversionResult(
        title=title.strip(),
        source_type="webpage",
        body=body.strip(),
        summary_seed=body,
        source_url=final_url,
        author=author,
        created_at=created_at,
        resources=resources,
        metadata={
            "extractor": winner.name,
            "extractor_score": round(winner.score, 2),
            "quality_status": winner.quality_status,
            "quality_reasons": winner.quality_reasons,
            "candidate_count": len([candidate for candidate in candidates if not candidate.hard_rejected]),
            "rendered": rendered_page is not None or rendered_candidates_added,
            "winner_source": winner.source,
            "top_candidates": top_candidate_metadata(candidates),
        },
    )


def should_use_specialized_result(result, current_body: str, rendered: bool) -> bool:
    if rendered:
        return True
    if result.score >= 900:
        return True
    return len(result.body) > len(current_body)


def collect_engine_candidates(
    html: str,
    soup: BeautifulSoup,
    analysis,
    rendered: bool = False,
) -> list[ExtractionCandidate]:
    candidates = engine_generate_candidates(html, soup, analysis)
    extractor_context = WebExtractorContext(
        soup=soup,
        base_url=analysis.url,
        title=analysis.title,
        rendered=rendered,
        metadata={"candidate_count": len(candidates)},
    )
    specialized_result = run_specialized_extractors(extractor_context)
    if specialized_result:
        candidate = engine_specialized_candidate(specialized_result, analysis)
        if candidate:
            candidates.append(candidate)
    return candidates


def maybe_add_snapshot_candidate(
    candidates: list[ExtractionCandidate],
    soup: BeautifulSoup,
    analysis,
    rendered: bool = False,
) -> None:
    best = engine_select_winner(candidates, analysis)
    if best and best.quality_status == "strong" and analysis.page_kind == "article":
        return
    if best and best.source == "specialized" and best.source_score >= 900:
        return
    if analysis.page_kind in {"home", "search", "list"} or not best or best.quality_status in {"weak", "blocked"}:
        snapshot = engine_snapshot_candidate(soup, analysis, rendered=rendered)
        if snapshot:
            candidates.append(snapshot)


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
    if is_challenge_or_js_disabled_text(visible_text(candidate.node)):
        return True
    return False


def should_use_snapshot_body(candidate: ContentCandidate, body: str, was_rendered: bool) -> bool:
    if candidate.name in {"body-fallback"}:
        return True
    if is_challenge_or_js_disabled_text(body):
        return True
    if not was_rendered:
        return False
    if candidate.score < 80 and len(body) < 3000:
        return True
    if candidate.name in {"trafilatura", "readability"} and len(re.findall(r"(?m)^#{1,6}\s+", body)) == 0:
        return True
    return False


def is_challenge_or_js_disabled_text(text: str) -> bool:
    lowered = normalize_text(text).lower()
    patterns = [
        "javascript is disabled",
        "javascript 不可用",
        "please enable javascript",
        "enable javascript",
        "checking your browser",
        "正在检查您的浏览器",
        "client challenge",
        "just a moment",
        "请启用 javascript",
        "request has been blocked",
    ]
    return any(pattern in lowered for pattern in patterns)


def access_restriction_message(body: str, title: str = "") -> str | None:
    text = normalize_text(f"{title} {body}")
    lowered = text.lower()
    markers = [
        "您当前请求存在异常",
        "暂时限制本次访问",
        "知乎小管家",
        '"code":40362',
        "access denied",
        "request has been blocked",
        "captcha",
        "bot challenge",
    ]
    if len(text) <= 1800 and any(marker in lowered or marker in text for marker in markers):
        return "Source site returned an access restriction or anti-bot challenge instead of public page content"
    return None


def should_render_after_fetch_error(exc: httpx.HTTPStatusError) -> bool:
    if not settings.web_render_enabled or async_playwright is None:
        return False
    return exc.response.status_code in {401, 402, 403, 406, 408, 409, 425, 429, 500, 502, 503, 504}


def should_render_after_request_error(exc: httpx.RequestError) -> bool:
    if not settings.web_render_enabled or async_playwright is None:
        return False
    return isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            httpx.TransportError,
        ),
    )


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
            try:
                context = await browser.new_context(
                    user_agent=FETCH_HEADERS["User-Agent"],
                    locale="zh-CN",
                    viewport={"width": 1366, "height": 900},
                    ignore_https_errors=settings.web_render_ignore_https_errors,
                )
                try:
                    page = await context.new_page()
                    checked_hosts: dict[tuple[str, str | None], bool] = {}

                    async def guard_request(route):
                        request_url = route.request.url
                        resource_type = route.request.resource_type
                        if resource_type in {"image", "media", "font"}:
                            with suppress(Exception):
                                await route.abort()
                            return
                        parsed = urlparse(request_url)
                        cache_key = (parsed.scheme, parsed.hostname)
                        try:
                            if cache_key not in checked_hosts:
                                assert_public_url(request_url)
                                checked_hosts[cache_key] = True
                        except Exception:
                            with suppress(Exception):
                                await route.abort()
                            return
                        with suppress(Exception):
                            await route.continue_()

                    await page.route("**/*", guard_request)
                    await goto_with_partial_dom(page, url, timeout_ms)
                    with suppress(Exception):
                        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 2000))
                    await page.wait_for_timeout(settings.web_render_wait_ms)
                    for _ in range(max(settings.web_render_scroll_steps, 0)):
                        with suppress(Exception):
                            await page.evaluate("window.scrollBy(0, Math.max(document.documentElement.clientHeight, 800))")
                        await page.wait_for_timeout(350)
                    final_url = page.url
                    assert_public_url(final_url)
                    title = await page.title()
                    html = await page.content()
                    if len(visible_text(BeautifulSoup(html, "html.parser"))) < 2:
                        return None
                    return RenderedPage(html=html, title=title, final_url=final_url)
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception:
        return None


async def goto_with_partial_dom(page, url: str, timeout_ms: int) -> None:
    try:
        await page.goto(url, wait_until="commit", timeout=timeout_ms)
    except Exception:
        with suppress(Exception):
            await page.wait_for_timeout(250)
        return
    with suppress(Exception):
        await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 3500))


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
        allowed_attrs = {
            "href",
            "src",
            "alt",
            "title",
            "datetime",
            "srcset",
            "data-src",
            "data-srcset",
            "data-original",
            "data-lazy-src",
            "data-image",
            "data-url",
            "width",
            "height",
        }
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


def meaningful_body(body: str) -> bool:
    text = re.sub(r"[\s#`|\\-]+", "", body)
    return len(text) > MIN_MEANINGFUL_TEXT_LENGTH




async def download_images(soup: BeautifulSoup, base_url: str, assets_dir: Path) -> list[str]:
    image_items: list[tuple[object, str]] = []
    images_by_url: dict[str, list[object]] = {}
    seen: set[str] = set()
    assets_dir.mkdir(parents=True, exist_ok=True)

    for img in soup.find_all("img"):
        if len(image_items) >= settings.max_images_per_job:
            break
        image_url = extract_image_source(img, base_url)
        if not image_url or is_placeholder_image(img, image_url):
            img.decompose()
            continue
        images_by_url.setdefault(image_url, []).append(img)
        if image_url in seen:
            continue
        seen.add(image_url)
        image_items.append((img, image_url))

    if not image_items:
        return []

    timeout = httpx.Timeout(settings.web_image_download_timeout_seconds, connect=min(settings.web_image_download_timeout_seconds, 2))
    limits = httpx.Limits(max_connections=max(settings.web_image_download_concurrency, 1))
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        max_redirects=2,
        headers=FETCH_HEADERS,
        limits=limits,
    ) as client:
        tasks = [fetch_image_asset(client, image_url) for _, image_url in image_items]
        fetched = await gather_with_budget(tasks, settings.web_image_download_budget_seconds)

    resources: list[str] = []
    downloaded_by_url: dict[str, str] = {}
    downloaded = 0
    for (img, image_url), result in zip(image_items, fetched):
        if isinstance(result, Exception) or not result:
            continue
        content_type, content = result
        if not content_type.startswith("image/") or len(content) > MAX_IMAGE_BYTES:
            continue
        try:
            extension = image_extension(content_type, image_url)
            downloaded += 1
            filename = f"image-{downloaded}{extension}"
            local_src = f"./assets/{filename}"
            (assets_dir / filename).write_bytes(content)
            downloaded_by_url[image_url] = local_src
            alt_text = normalize_text(img.get("alt", ""))
            suffix = f"（{alt_text}）" if alt_text else ""
            resources.append(f"- 图片资源：{local_src}{suffix}")
        except Exception:
            continue

    rewrite_image_sources(images_by_url, downloaded_by_url)
    return resources


async def fetch_image_asset(client: httpx.AsyncClient, image_url: str) -> tuple[str, bytes] | None:
    try:
        assert_public_url(image_url)
        response = await client.get(image_url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if len(response.content) > MAX_IMAGE_BYTES:
            return None
        return content_type, response.content
    except Exception:
        return None


async def gather_with_budget(tasks, timeout_seconds: float) -> list:
    gather_future = asyncio.gather(*tasks, return_exceptions=True)
    try:
        return await asyncio.wait_for(gather_future, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        gather_future.cancel()
        with suppress(asyncio.CancelledError):
            await gather_future
        return [None] * len(tasks)


def rewrite_image_sources(images_by_url: dict[str, list[object]], downloaded_by_url: dict[str, str]) -> None:
    for image_url, images in images_by_url.items():
        src = downloaded_by_url.get(image_url, image_url)
        for img in images:
            img["src"] = src


def image_extension(content_type: str, image_url: str) -> str:
    extension = mimetypes.guess_extension(content_type) or Path(urlparse(image_url).path).suffix or ".img"
    if extension in {".jpe", ".jpeg"}:
        return ".jpg"
    return extension
