import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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


@dataclass
class BilibiliVideoDetail:
    title: str
    url: str
    bvid: str | None = None
    aid: str | None = None
    author: str | None = None
    author_url: str | None = None
    published_at: str | None = None
    duration: str | None = None
    description: str | None = None
    cover_url: str | None = None
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    stats: dict[str, str] = field(default_factory=dict)


VIDEO_STAT_LABELS = {
    "view": "播放",
    "danmaku": "弹幕",
    "reply": "评论",
    "like": "点赞",
    "coin": "投币",
    "favorite": "收藏",
    "share": "分享",
}

GENERIC_KEYWORDS = {"哔哩哔哩", "bilibili", "B站", "弹幕"}


def extract(context: WebExtractorContext) -> WebExtractorResult | None:
    if is_bilibili_video_page(context.base_url):
        detail = extract_video_detail(context.soup, context.base_url, context.title)
        if not detail:
            return None
        body = render_bilibili_video_markdown(detail)
        return WebExtractorResult(
            name="bilibili-video",
            body=body,
            score=920,
            metadata={"bvid": detail.bvid, "tag_count": len(detail.tags)},
        )

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


def is_bilibili_domain(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    return host == "bilibili.com" or host.endswith(".bilibili.com")


def is_bilibili_home(url: str) -> bool:
    parsed = urlparse(url)
    return is_bilibili_domain(url) and parsed.path in {"", "/"}


def is_bilibili_video_page(url: str) -> bool:
    parsed = urlparse(url)
    return is_bilibili_domain(url) and parsed.path.startswith("/video/")


def extract_video_detail(soup: BeautifulSoup, base_url: str, fallback_title: str = "") -> BilibiliVideoDetail | None:
    state = extract_initial_state(soup) or {}
    video_data = dict_value(state.get("videoData"))
    up_data = dict_value(state.get("upData"))

    title = normalize_text(
        str_value(video_data.get("title"))
        or text_from_first(soup, [".video-title", ".video-info-title", "h1"])
        or clean_bilibili_title(fallback_title)
    )
    if not title:
        return None

    owner = dict_value(video_data.get("owner"))
    owner_mid = str_value(owner.get("mid") or up_data.get("mid"))
    author = normalize_text(
        str_value(owner.get("name"))
        or str_value(up_data.get("name"))
        or meta_content(soup, "author")
        or text_from_first(soup, [".up-name"])
        or ""
    )
    author_url = f"https://space.bilibili.com/{owner_mid}" if owner_mid else None

    tags = extract_video_tags(state, soup, title)
    stats = extract_video_stats(video_data)
    description = normalize_text(str_value(video_data.get("desc")) or text_from_first(soup, [".desc-info-text", ".basic-desc-info"]) or "")
    category = normalize_text(str_value(video_data.get("tname_v2")) or str_value(video_data.get("tname")) or "")
    cover_url = normalize_media_url(str_value(video_data.get("pic")) or meta_content(soup, "og:image"), base_url)

    return BilibiliVideoDetail(
        title=title,
        url=base_url,
        bvid=str_value(video_data.get("bvid")) or bvid_from_url(base_url),
        aid=str_value(video_data.get("aid")),
        author=author or None,
        author_url=author_url,
        published_at=format_timestamp(video_data.get("pubdate")),
        duration=format_duration(video_data.get("duration")),
        description=description or None,
        cover_url=cover_url,
        category=category or None,
        tags=tags,
        stats=stats,
    )


def extract_initial_state(soup: BeautifulSoup) -> dict | None:
    decoder = json.JSONDecoder()
    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        match = re.search(r"window\.__INITIAL_STATE__\s*=", text)
        if not match:
            continue
        try:
            data, _end = decoder.raw_decode(text[match.end() :].lstrip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def dict_value(value) -> dict:
    return value if isinstance(value, dict) else {}


def str_value(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def meta_content(soup: BeautifulSoup, key: str) -> str | None:
    tag = soup.find("meta", attrs={"name": key}) or soup.find("meta", attrs={"property": key})
    if not tag:
        return None
    content = tag.get("content")
    return normalize_text(content) if isinstance(content, str) else None


def clean_bilibili_title(title: str) -> str:
    return normalize_text(re.sub(r"_哔哩哔哩_bilibili\s*$", "", title or ""))


def bvid_from_url(url: str) -> str | None:
    match = re.search(r"/video/([^/?#]+)/?", url)
    return match.group(1) if match else None


def extract_video_tags(state: dict, soup: BeautifulSoup, title: str) -> list[str]:
    state_tags = []
    for item in state.get("tags", []):
        if isinstance(item, dict):
            name = normalize_text(str_value(item.get("tag_name")) or "")
            if name:
                state_tags.append(name)
    if state_tags:
        return unique_preserve_order(state_tags)[:20]

    dom_tags = [
        normalize_text(node.get_text(" ", strip=True))
        for node in soup.select(".tag-link, .video-tag-container a")
        if normalize_text(node.get_text(" ", strip=True))
    ]
    if dom_tags:
        return unique_preserve_order(dom_tags)[:20]

    keywords = meta_content(soup, "keywords") or ""
    candidates = [normalize_text(item) for item in keywords.split(",")]
    title_key = normalize_text(title)
    return unique_preserve_order(
        item for item in candidates if item and item != title_key and item not in GENERIC_KEYWORDS
    )[:20]


def extract_video_stats(video_data: dict) -> dict[str, str]:
    raw_stats = dict_value(video_data.get("stat"))
    stats: dict[str, str] = {}
    for key, label in VIDEO_STAT_LABELS.items():
        value = raw_stats.get(key)
        if value is None:
            continue
        stats[label] = str(value)
    return stats


def format_timestamp(value) -> str | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    china_tz = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(timestamp, tz=china_tz).strftime("%Y-%m-%d %H:%M:%S %z")


def format_duration(value) -> str | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


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


def normalize_media_url(value: str | None, base_url: str) -> str | None:
    if not value:
        return None
    if value.startswith("//"):
        return "https:" + value
    absolute = urljoin(base_url, value)
    if absolute.startswith("http://i") or absolute.startswith("http://upos-"):
        return "https://" + absolute.removeprefix("http://")
    return absolute


def render_bilibili_video_markdown(detail: BilibiliVideoDetail) -> str:
    info = [
        f"- 标题：{detail.title}",
        f"- UP 主：{author_markdown(detail)}" if detail.author else "",
        f"- BV 号：{detail.bvid}" if detail.bvid else "",
        f"- AV 号：{detail.aid}" if detail.aid else "",
        f"- 分区：{detail.category}" if detail.category else "",
        f"- 发布时间：{detail.published_at}" if detail.published_at else "",
        f"- 时长：{detail.duration}" if detail.duration else "",
        f"- 原始链接：{detail.url}",
        f"- 封面：{detail.cover_url}" if detail.cover_url else "",
    ]
    sections = ["## 视频信息\n\n" + "\n".join(item for item in info if item)]
    if detail.description:
        sections.append("## 简介\n\n" + detail.description)
    if detail.stats:
        rows = ["| 指标 | 数值 |", "| --- | --- |"]
        rows.extend(f"| {escape_cell(label)} | {escape_cell(value)} |" for label, value in detail.stats.items())
        sections.append("## 数据\n\n" + "\n".join(rows))
    if detail.tags:
        sections.append("## 标签\n\n" + "\n".join(f"- {tag}" for tag in detail.tags))
    return "\n\n".join(sections).strip()


def author_markdown(detail: BilibiliVideoDetail) -> str:
    if detail.author_url:
        return f"[{detail.author}]({detail.author_url})"
    return detail.author or ""


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
