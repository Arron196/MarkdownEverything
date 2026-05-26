from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.converters.web_extractors.base import WebExtractorContext, WebExtractorResult
from app.converters.web_extractors.utils import normalize_text, unique_preserve_order


@dataclass
class BilibiliVideo:
    title: str
    url: str
    author: str | None = None
    published_at: str | None = None
    views: str | None = None
    interactions: str | None = None
    duration: str | None = None


def extract(context: WebExtractorContext) -> WebExtractorResult | None:
    if not is_bilibili_home(context.base_url):
        return None
    videos = extract_home_videos(context.soup, context.base_url)
    if len(videos) < 3:
        return None
    body = render_bilibili_home_markdown(videos)
    return WebExtractorResult(
        name="bilibili-home",
        body=body,
        score=850,
        metadata={"video_count": len(videos)},
    )


def is_bilibili_home(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.endswith("bilibili.com") and parsed.path in {"", "/"}


def extract_home_videos(soup: BeautifulSoup, base_url: str) -> list[BilibiliVideo]:
    videos: list[BilibiliVideo] = []
    seen: set[str] = set()
    for card in soup.select(".feed-card, .bili-video-card.is-rcmd"):
        video = video_from_card(card, base_url)
        if not video or video.url in seen:
            continue
        seen.add(video.url)
        videos.append(video)
        if len(videos) >= 50:
            break
    return videos


def video_from_card(card, base_url: str) -> BilibiliVideo | None:
    title_node = card.select_one(".bili-video-card__info--tit")
    link_node = title_node.select_one("a[href]") if title_node else None
    if not link_node:
        link_node = card.select_one("a[href*='/video/'], a[href*='bilibili.com/video/']")
    href = link_node.get("href") if link_node else None
    if not isinstance(href, str) or "/video/" not in href:
        return None

    title = normalize_text(
        (title_node.get("title") if title_node else None)
        or link_node.get("title")
        or link_node.get_text(" ", strip=True)
    )
    image = card.select_one("img[alt]")
    if not title and image:
        title = normalize_text(image.get("alt") or "")
    if not title or title in {"视频", "播放"}:
        return None

    stats = [
        normalize_text(node.get_text(" ", strip=True))
        for node in card.select(".bili-video-card__stats--item, .bili-video-card__stats--text")
    ]
    stats = unique_preserve_order(value for value in stats if value)
    author = text_from_first(card, [".bili-video-card__info--author"])
    published_at = text_from_first(card, [".bili-video-card__info--date"])
    if published_at:
        published_at = published_at.removeprefix("·").strip()
    duration = text_from_first(card, [".bili-video-card__stats__duration"])

    return BilibiliVideo(
        title=title,
        url=normalize_bilibili_url(href, base_url),
        author=author,
        published_at=published_at,
        views=stats[0] if len(stats) >= 1 else None,
        interactions=stats[1] if len(stats) >= 2 else None,
        duration=duration,
    )


def text_from_first(card, selectors: list[str]) -> str | None:
    for selector in selectors:
        node = card.select_one(selector)
        if node:
            text = normalize_text(node.get("title") or node.get_text(" ", strip=True))
            if text:
                return text
    return None


def normalize_bilibili_url(href: str, base_url: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    return urljoin(base_url, href)


def render_bilibili_home_markdown(videos: list[BilibiliVideo]) -> str:
    lines = [
        "## 首页推荐视频",
        "",
        "| 标题 | 作者 | 播放 | 互动 | 时长 | 日期 | 链接 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for video in videos:
        lines.append(
            "| {title} | {author} | {views} | {interactions} | {duration} | {published_at} | {url} |".format(
                title=escape_cell(video.title),
                author=escape_cell(video.author),
                views=escape_cell(video.views),
                interactions=escape_cell(video.interactions),
                duration=escape_cell(video.duration),
                published_at=escape_cell(video.published_at),
                url=escape_cell(video.url),
            )
        )
    return "\n".join(lines)


def escape_cell(value: str | None) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ").strip()
