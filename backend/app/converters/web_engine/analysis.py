import json
import re
from difflib import SequenceMatcher
from html import unescape
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from app.converters.web_extractors.utils import normalize_text, visible_text
from app.converters.web_engine.models import PageAnalysis, TitleCandidate

UI_TITLE_TERMS = [
    "search",
    "sign in",
    "sign up",
    "log in",
    "login",
    "home",
    "homepage",
    "navigation",
    "toggle",
    "menu",
    "登录",
    "注册",
    "搜索",
    "首页",
    "导航",
    "菜单",
]

ACCESS_MARKERS = [
    "您当前请求存在异常",
    "暂时限制本次访问",
    "知乎小管家",
    '"code":40362',
    "access denied",
    "request has been blocked",
    "forbidden",
    "captcha",
    "bot challenge",
    "安全验证",
    "人机验证",
]

LOGIN_MARKERS = [
    "login required",
    "sign in to continue",
    "log in to continue",
    "请先登录",
    "登录后查看",
    "登录或注册",
]

JS_MARKERS = [
    "javascript is disabled",
    "javascript 不可用",
    "please enable javascript",
    "enable javascript",
    "checking your browser",
    "正在检查您的浏览器",
    "client challenge",
    "just a moment",
    "请启用 javascript",
]


def analyze_page(
    soup: BeautifulSoup,
    url: str,
    rendered_title: str | None = None,
    status_code: int | None = None,
) -> PageAnalysis:
    text = visible_text(soup.body or soup)
    metadata = extract_metadata(soup)
    title_candidates = collect_title_candidates(soup, url, metadata, rendered_title)
    blocked_reason = detect_blocked_reason(text, title_candidates, status_code)
    page_kind = classify_page_kind(soup, url, text)
    return PageAnalysis(
        url=url,
        title_candidates=title_candidates,
        metadata=metadata,
        blocked_reason=blocked_reason,
        visible_text_length=len(text),
        dom_size=len(soup.find_all(True)),
        dom_has_spa_signature=dom_has_spa_signature(soup, text),
        page_kind=page_kind,
    )


def extract_metadata(soup: BeautifulSoup) -> dict:
    metadata: dict[str, object] = {}
    for tag in soup.find_all("meta"):
        key = tag.get("property") or tag.get("name") or tag.get("itemprop")
        value = tag.get("content")
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            metadata[key.strip().lower()] = normalize_text(value)
    jsonld = extract_json_ld_items(soup)
    if jsonld:
        metadata["jsonld"] = jsonld
        for item in jsonld:
            for source_key, target_key in [
                ("headline", "jsonld:title"),
                ("name", "jsonld:name"),
                ("author", "jsonld:author"),
                ("datePublished", "jsonld:datePublished"),
                ("dateCreated", "jsonld:dateCreated"),
            ]:
                value = json_ld_scalar(item.get(source_key))
                if value and target_key not in metadata:
                    metadata[target_key] = value
    return metadata


def collect_title_candidates(
    soup: BeautifulSoup,
    url: str,
    metadata: dict,
    rendered_title: str | None = None,
) -> list[TitleCandidate]:
    raw: list[TitleCandidate] = []
    if rendered_title:
        raw.append(TitleCandidate(clean_title(rendered_title), "rendered_title"))
    if soup.title:
        raw.append(TitleCandidate(clean_title(soup.title.get_text(" ", strip=True)), "title"))
    for selector, source in [
        ("h1", "h1"),
        ("[property='og:title']", "og:title"),
        ("[name='twitter:title']", "twitter:title"),
    ]:
        if selector.startswith("["):
            continue
        for tag in soup.select(selector)[:4]:
            raw.append(TitleCandidate(clean_title(tag.get_text(" ", strip=True)), source))
    for key in ["og:title", "twitter:title", "jsonld:title", "jsonld:name"]:
        value = metadata.get(key)
        if isinstance(value, str):
            raw.append(TitleCandidate(clean_title(value), key))

    by_key: dict[str, TitleCandidate] = {}
    for candidate in raw:
        if not candidate.value:
            continue
        key = comparable(candidate.value)
        candidate.score, candidate.reasons = score_title_candidate(candidate.value, candidate.source, soup, url)
        existing = by_key.get(key)
        if existing is None or candidate.score > existing.score:
            by_key[key] = candidate

    result = list(by_key.values())
    if not result:
        fallback = TitleCandidate(urlparse(url).netloc or "Untitled", "url", score=0)
        result.append(fallback)
    return sorted(result, key=lambda item: item.score, reverse=True)


def score_title_candidate(value: str, source: str, soup: BeautifulSoup, url: str) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    if source in {"h1"}:
        score += 24
        reasons.append("h1_title")
        if is_near_main_h1(value, soup):
            score += 40
            reasons.append("near_main_h1")
    if source in {"og:title", "twitter:title", "jsonld:title"}:
        score += 30
        reasons.append("metadata_title")
    if source == "jsonld:name":
        score += 8
        reasons.append("jsonld_name")
    slug_similarity = similarity(value, url_slug_text(url))
    score += 20 * slug_similarity
    if slug_similarity >= 0.45:
        reasons.append("url_slug_match")
    if is_ui_text(value):
        score -= 50
        reasons.append("ui_text")
    if len(value) < 3 or len(value) > 180:
        score -= 30
        reasons.append("bad_length")
    return score, reasons


def clean_title(title: str) -> str:
    title = normalize_text(title)
    title = title.replace("[ 编辑 ]", "").replace("[编辑]", "")
    title = re.sub(r"\s+[-|·]\s+.*$", "", title)
    return title.strip()


def is_near_main_h1(value: str, soup: BeautifulSoup) -> bool:
    normalized = comparable(value)
    for h1 in soup.find_all("h1")[:4]:
        if comparable(h1.get_text(" ", strip=True)) != normalized:
            continue
        parent_names = [parent.name for parent in h1.parents if getattr(parent, "name", None)][:4]
        return any(name in {"main", "article", "body"} for name in parent_names)
    return False


def is_ui_text(value: str) -> bool:
    lowered = normalize_text(value).lower()
    if not lowered:
        return True
    if lowered in UI_TITLE_TERMS:
        return True
    return any(term in lowered for term in UI_TITLE_TERMS) and len(lowered) <= 80


def detect_blocked_reason(
    text: str,
    title_candidates: list[TitleCandidate],
    status_code: int | None = None,
) -> str | None:
    combined = normalize_text(" ".join([*(item.value for item in title_candidates[:3]), text]))
    lowered = combined.lower()
    if status_code in {401, 403}:
        return "access_denied"
    if status_code in {429}:
        return "access_denied"
    if len(combined) <= 4000 and any(marker in lowered or marker in combined for marker in ACCESS_MARKERS):
        if "captcha" in lowered or "验证" in combined:
            return "captcha"
        return "access_denied"
    if len(combined) <= 3000 and any(marker in lowered or marker in combined for marker in LOGIN_MARKERS):
        return "login"
    if any(marker in lowered or marker in combined for marker in JS_MARKERS):
        return "js_required"
    if len(combined) < 20:
        return "js_required"
    return None


def dom_has_spa_signature(soup: BeautifulSoup, text: str) -> bool:
    script_count = len(soup.find_all("script"))
    root_ids = {"root", "__next", "app", "q-app", "svelte", "gatsby-focus-wrapper"}
    has_root = any(soup.find(id=root_id) is not None for root_id in root_ids)
    has_bundle = any(
        isinstance(tag.get("src"), str) and re.search(r"(chunk|bundle|webpack|vite|_next/static)", tag.get("src", ""), re.I)
        for tag in soup.find_all("script")
    )
    return (has_root and len(text) < 800) or (script_count >= 12 and len(text) < 500) or (has_bundle and len(text) < 350)


def classify_page_kind(soup: BeautifulSoup, url: str, text: str) -> str:
    path = urlparse(url).path.strip("/")
    lowered_url = unquote(url).lower()
    search_form = soup.find("form", attrs={"role": "search"}) or soup.find("input", attrs={"type": "search"})
    if search_form or re.search(r"(^|[?&])(q|query|search)=", lowered_url):
        return "search"
    links = soup.find_all("a")
    paragraphs = soup.find_all("p")
    if (not path or path in {"search", "s"}) and len(links) >= max(10, len(paragraphs) * 3):
        return "home"
    if len(links) >= 30 and len(paragraphs) < 5 and len(text) < 4000:
        return "list"
    return "article"


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
    return items[:20]


def json_ld_scalar(value) -> str | None:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, dict):
        for key in ["name", "headline", "@id", "url"]:
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return normalize_text(nested)
    if isinstance(value, list):
        values = [json_ld_scalar(item) for item in value[:3]]
        values = [item for item in values if item]
        if values:
            return ", ".join(values)
    return None


def json_ld_type(item: dict) -> str | None:
    value = item.get("@type")
    if isinstance(value, list):
        return ", ".join(str(part) for part in value if part)
    if isinstance(value, str):
        return value
    return None


def url_slug_text(url: str) -> str:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if not parts:
        return parsed.netloc
    return re.sub(r"[-_]+", " ", parts[-1])


def similarity(left: str, right: str) -> float:
    left_key = comparable(left)
    right_key = comparable(right)
    if not left_key or not right_key:
        return 0.0
    return SequenceMatcher(None, left_key, right_key).ratio()


def comparable(value: str) -> str:
    return re.sub(r"[\W_]+", "", normalize_text(value), flags=re.UNICODE).lower()
