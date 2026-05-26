import re
from collections import Counter
from difflib import SequenceMatcher

from app.converters.web_extractors.utils import extract_image_source, is_placeholder_image, normalize_text, visible_text
from app.converters.web_engine.analysis import comparable
from app.converters.web_engine.models import CandidateMetrics, ExtractionCandidate, PageAnalysis

NOISE_TERMS = [
    "navigation",
    "table of contents",
    "on this page",
    "sign in",
    "sign up",
    "log in",
    "login",
    "subscribe",
    "newsletter",
    "cookie",
    "advertisement",
    "sponsored",
    "share",
    "follow us",
    "related posts",
    "recommended",
    "在此页面",
    "目录",
    "搜索",
    "登录",
    "注册",
    "订阅",
    "广告",
    "分享",
    "相关推荐",
]

SOURCE_PRIORITY = {
    "specialized": 7,
    "semantic": 6,
    "trafilatura": 5,
    "readability": 4,
    "jsonld": 3,
    "heuristic": 2,
    "snapshot": 1,
}


def score_candidate(candidate: ExtractionCandidate, analysis: PageAnalysis) -> ExtractionCandidate:
    candidate.metrics = calculate_metrics(candidate, analysis)
    metrics = candidate.metrics
    if should_hard_reject(metrics):
        candidate.hard_rejected = True
        candidate.score = -10000
        candidate.quality_status = "rejected"
        candidate.quality_reasons = ["hard_constraint_too_short"]
        return candidate

    content_value = min(metrics.L, 30000) / 90
    content_value += 4.0 * min(metrics.P, 80)
    content_value += 1.0 * min(metrics.LI, 100)

    structure_value = 7.0 * min(metrics.H, 40)
    structure_value += 16.0 * min(metrics.TB, 15)
    structure_value += 18.0 * min(metrics.CB, 25)
    structure_value += 2.0 * min(metrics.IM, 30)
    structure_value += 5.0 * min(metrics.BQ, 10)
    structure_value += min(metrics.IC, 2000) / 80

    metadata_fit = 40 * metrics.TS + 25 * metrics.MF

    noise_cost = 22 * metrics.N
    noise_cost += 180 * max(0, metrics.rho - 0.45)
    if metrics.rho > 0.60 and metrics.P < 4:
        noise_cost += 260
    if metrics.H == 0 and metrics.P < 3 and metrics.L < 900:
        noise_cost += 120
    metrics.noise_cost = noise_cost

    duplication_cost = 180 * max(0, 0.75 - metrics.U) + 120 * metrics.R
    metrics.duplication_cost = duplication_cost

    candidate.score = (
        content_value
        + structure_value
        + metadata_fit
        - noise_cost
        - duplication_cost
        + source_bonus(candidate)
    )
    if candidate.source == "snapshot":
        candidate.score = min(candidate.score, 75)
    candidate.quality_status, candidate.quality_reasons = classify_quality(candidate, analysis)
    return candidate


def calculate_metrics(candidate: ExtractionCandidate, analysis: PageAnalysis) -> CandidateMetrics:
    node = candidate.node
    text = visible_text(node) if node is not None else markdown_visible_text(candidate.markdown)
    metrics = CandidateMetrics()
    metrics.L = len(text)
    if node is not None:
        metrics.P = count_text_tags(node, ["p"], min_length=40)
        metrics.H = len({normalize_text(tag.get_text(" ", strip=True)) for tag in node.find_all(re.compile(r"^h[1-6]$")) if visible_text(tag)})
        metrics.LI = count_text_tags(node, ["li"], min_length=25)
        metrics.TB = len(node.find_all("table"))
        metrics.CB = len([pre for pre in node.find_all("pre") if len(pre.get_text("", strip=True)) >= 24])
        metrics.IC = sum(
            len(code.get_text("", strip=True))
            for code in node.find_all("code")
            if code.find_parent("pre") is None
        )
        metrics.IM = count_images(node, analysis.url)
        metrics.BQ = len(node.find_all("blockquote"))
        metrics.A = sum(len(visible_text(anchor)) for anchor in node.find_all("a"))
    else:
        metrics.P = len([line for line in candidate.markdown.splitlines() if len(normalize_text(line)) >= 40 and not line.lstrip().startswith(("#", "-", "|"))])
        metrics.H = len(set(re.findall(r"(?m)^#{1,6}\s+(.+)$", candidate.markdown)))
        metrics.LI = len([line for line in candidate.markdown.splitlines() if re.match(r"\s*[-*]\s+\S.{24,}", line)])
        metrics.TB = len(re.findall(r"(?m)^\|.+\|$", candidate.markdown))
        metrics.CB = candidate.markdown.count("```") // 2
        metrics.IC = sum(len(match) for match in re.findall(r"`([^`\n]{1,200})`", candidate.markdown))
        metrics.IM = len(re.findall(r"!\[[^\]]*]\([^)]+\)", candidate.markdown))
        metrics.BQ = len(re.findall(r"(?m)^>\s+", candidate.markdown))
        metrics.A = sum(len(match.group(1)) for match in re.finditer(r"\[([^\]]+)]\([^)]+\)", candidate.markdown))
    metrics.rho = metrics.A / max(metrics.L, 1)
    metrics.U = unique_ngram_ratio(text)
    metrics.N = noise_marker_count(candidate, text)
    metrics.R = repeated_block_ratio(candidate, text)
    metrics.TS = title_similarity(candidate, analysis)
    metrics.MF = metadata_fit(candidate, analysis)
    return metrics


def classify_quality(candidate: ExtractionCandidate, analysis: PageAnalysis) -> tuple[str, list[str]]:
    if analysis.blocked_reason:
        return "blocked", [analysis.blocked_reason]
    metrics = candidate.metrics
    reasons: list[str] = []
    if metrics.P >= 3:
        reasons.append("paragraph_density")
    if metrics.rho <= 0.45:
        reasons.append("low_link_density")
    if metrics.H or metrics.TB or metrics.CB:
        reasons.append("structured_content")
    if metrics.TS >= 0.35 or metrics.MF >= 0.5:
        reasons.append("metadata_fit")
    if metrics.N >= 8 and metrics.P < 5:
        reasons.append("high_noise_weak")
        return "weak", reasons
    if metrics.rho > 0.75 and metrics.P < 3 and candidate.source != "snapshot":
        reasons.append("navigation_link_density")
        return "weak", reasons
    if candidate.source == "snapshot" and analysis.page_kind in {"home", "search", "list"} and candidate.score >= 55:
        reasons.append(f"{analysis.page_kind}_snapshot")
        return "usable", reasons
    if (
        candidate.score >= 85
        and metrics.L >= 600
        and metrics.P >= 3
        and metrics.rho <= 0.55
        and metrics.N <= 5
        and candidate.source != "snapshot"
    ):
        return "strong", reasons or ["strong_score"]
    if candidate.score >= 55 and metrics.L >= 200:
        return "usable", reasons or ["usable_score"]
    if candidate.score >= 25:
        return "weak", reasons or ["weak_score"]
    return "weak", reasons or ["low_score"]


def source_bonus(candidate: ExtractionCandidate) -> float:
    if candidate.source == "specialized":
        return 900 if candidate.source_score >= 900 else candidate.source_score
    if candidate.source == "semantic":
        return 40 if candidate.name in {"article", "main", "main article", "[role='main'] article", "[role='main']", "#content"} else 12
    if candidate.source == "readability":
        return 28
    if candidate.source == "trafilatura":
        return 30
    if candidate.source == "jsonld":
        return 35
    if candidate.source == "heuristic":
        return -5
    return 0


def should_hard_reject(metrics: CandidateMetrics) -> bool:
    return metrics.L < 80 and metrics.TB == 0 and metrics.CB == 0 and metrics.IM == 0


def count_text_tags(node, names: list[str], min_length: int) -> int:
    return sum(1 for tag in node.find_all(names) if len(visible_text(tag)) >= min_length)


def count_images(node, base_url: str) -> int:
    count = 0
    for img in node.find_all("img"):
        image_url = extract_image_source(img, base_url)
        if image_url and not is_placeholder_image(img, image_url):
            count += 1
    return count


def unique_ngram_ratio(text: str) -> float:
    normalized = normalize_text(text)
    if len(normalized) < 60:
        return 1.0
    if contains_cjk(normalized):
        units = [char for char in normalized if not char.isspace()]
    else:
        units = re.findall(r"\w+", normalized.lower(), flags=re.UNICODE)
    if len(units) < 8:
        return 1.0
    grams = [tuple(units[index : index + 5]) for index in range(max(0, len(units) - 4))]
    if len(grams) < 5:
        return 1.0
    return len(set(grams)) / len(grams)


def repeated_block_ratio(candidate: ExtractionCandidate, text: str) -> float:
    if candidate.node is not None:
        blocks = [
            normalize_text(tag.get_text(" ", strip=True))
            for tag in candidate.node.find_all(["p", "li", "h1", "h2", "h3", "blockquote"])
            if len(normalize_text(tag.get_text(" ", strip=True))) >= 20
        ]
    else:
        blocks = [normalize_text(line) for line in candidate.markdown.splitlines() if len(normalize_text(line)) >= 20]
    if len(blocks) < 4:
        return 0.0
    counts = Counter(blocks)
    repeated = sum(count for count in counts.values() if count > 1)
    return repeated / len(blocks)


def title_similarity(candidate: ExtractionCandidate, analysis: PageAnalysis) -> float:
    candidate_head = first_heading_or_paragraph(candidate)
    if not candidate_head:
        return 0.0
    scores = []
    for title in analysis.title_candidates[:5]:
        left = comparable(candidate_head)
        right = comparable(title.value)
        if left and right:
            scores.append(SequenceMatcher(None, left, right).ratio())
    return max(scores, default=0.0)


def metadata_fit(candidate: ExtractionCandidate, analysis: PageAnalysis) -> float:
    text = comparable(first_heading_or_paragraph(candidate) + " " + markdown_visible_text(candidate.markdown)[:600])
    if not text:
        return 0.0
    matches = 0
    total = 0
    for key in ["og:title", "twitter:title", "jsonld:title", "jsonld:name", "author", "article:author", "jsonld:author"]:
        value = analysis.metadata.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        total += 1
        value_key = comparable(value)
        if value_key and (value_key in text or SequenceMatcher(None, value_key, text[: max(len(value_key) * 2, 80)]).ratio() > 0.45):
            matches += 1
    if total == 0:
        return 0.0
    return min(matches / total, 1.0)


def first_heading_or_paragraph(candidate: ExtractionCandidate) -> str:
    if candidate.node is not None:
        for tag in candidate.node.find_all(["h1", "h2", "h3", "p"]):
            text = normalize_text(tag.get_text(" ", strip=True))
            if text:
                return text
    for pattern in [r"(?m)^#{1,6}\s+(.+)$", r"(?m)^(.{20,240})$"]:
        match = re.search(pattern, candidate.markdown)
        if match:
            return normalize_text(match.group(1))
    return ""


def noise_marker_count(candidate: ExtractionCandidate, text: str) -> int:
    count = 0
    if candidate.node is not None:
        for tag in candidate.node.find_all(["a", "button", "nav", "aside", "footer", "header", "form", "li", "div", "section"]):
            block_text = visible_text(tag)
            attrs = " ".join(str(value).lower() for value in tag.attrs.values())
            tag_is_ui = tag.name in {"a", "button", "nav", "aside", "footer", "header", "form"}
            likely_ui_block = tag_is_ui or len(block_text) <= 120 or bool(
                re.search(r"(nav|sidebar|toc|breadcrumb|footer|share|social|advert|cookie|login|signin|signup)", attrs)
            )
            if likely_ui_block:
                lowered = block_text.lower()
                count += sum(lowered.count(term.lower()) for term in NOISE_TERMS)
            if re.search(r"(nav|sidebar|toc|breadcrumb|footer|share|social|advert|cookie|login|signin|signup)", attrs):
                count += 1
    else:
        for line in candidate.markdown.splitlines():
            if len(normalize_text(line)) > 120:
                continue
            lowered = line.lower()
            count += sum(lowered.count(term.lower()) for term in NOISE_TERMS)
    return min(count, 50)


def markdown_visible_text(markdown: str) -> str:
    text = re.sub(r"```.*?```", " ", markdown, flags=re.S)
    text = re.sub(r"!\[([^\]]*)]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", text)
    text = re.sub(r"[#>*_`|\\-]+", " ", text)
    return normalize_text(text)


def contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text))
