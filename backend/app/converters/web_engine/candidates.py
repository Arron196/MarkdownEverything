import re
from copy import deepcopy
from html import escape

import trafilatura
from bs4 import BeautifulSoup

from app.converters.web_extractors.base import WebExtractorResult
from app.converters.web_extractors.snapshot import build_page_snapshot, render_page_snapshot_markdown
from app.converters.web_extractors.utils import clean_markdown, markdown_from_node, normalize_links, normalize_text, visible_text
from app.converters.web_engine.analysis import json_ld_scalar, json_ld_type
from app.converters.web_engine.cleaning import CONTENT_SELECTORS, MIN_MEANINGFUL_TEXT_LENGTH, clean_content_node, is_noisy_node
from app.converters.web_engine.models import ExtractionCandidate, PageAnalysis
from app.converters.web_engine.scoring import score_candidate

try:
    from readability import Document as ReadabilityDocument
except Exception:  # pragma: no cover - optional dependency in local dev environments.
    ReadabilityDocument = None


ARTICLE_JSONLD_TYPES = {"article", "newsarticle", "blogposting", "techarticle", "report"}


def generate_candidates(html: str, soup: BeautifulSoup, analysis: PageAnalysis) -> list[ExtractionCandidate]:
    candidates: list[ExtractionCandidate] = []
    seen: set[str] = set()

    for candidate in semantic_candidates(soup, analysis):
        add_candidate(candidates, seen, candidate, analysis)
    for candidate in extractor_candidates(html, analysis):
        add_candidate(candidates, seen, candidate, analysis)
    for candidate in jsonld_candidates(soup, analysis):
        add_candidate(candidates, seen, candidate, analysis)
    for candidate in heuristic_candidates(soup, analysis):
        add_candidate(candidates, seen, candidate, analysis)
    if not candidates:
        fallback = body_fallback_candidate(soup, analysis)
        if fallback:
            candidates.append(score_candidate(fallback, analysis))
    return [candidate for candidate in candidates if not candidate.hard_rejected]


def specialized_candidate(result: WebExtractorResult, analysis: PageAnalysis) -> ExtractionCandidate | None:
    body = clean_markdown(result.body, result.metadata.get("title") or analysis.title)
    if not body.strip():
        return None
    candidate = ExtractionCandidate(
        name=result.name,
        source="specialized",
        node=None,
        markdown=body,
        metadata={**dict(result.metadata or {}), "base_url": analysis.url},
        source_score=result.score,
    )
    return score_candidate(candidate, analysis)


def snapshot_candidate(soup: BeautifulSoup, analysis: PageAnalysis, rendered: bool = False) -> ExtractionCandidate | None:
    snapshot = build_page_snapshot(soup, analysis.url, analysis.title)
    markdown = render_page_snapshot_markdown(snapshot)
    if not markdown.strip():
        return None
    candidate = ExtractionCandidate(
        name="rendered-snapshot" if rendered else "static-snapshot",
        source="snapshot",
        node=None,
        markdown=clean_markdown(markdown, analysis.title),
        metadata={"page_kind": analysis.page_kind, "base_url": analysis.url},
        source_score=100 if rendered else 50,
    )
    return score_candidate(candidate, analysis)


def semantic_candidates(soup: BeautifulSoup, analysis: PageAnalysis) -> list[ExtractionCandidate]:
    candidates: list[ExtractionCandidate] = []
    seen_nodes: set[int] = set()
    for selector, _bonus in CONTENT_SELECTORS:
        for node in soup.select(selector)[:8]:
            node_id = id(node)
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)
            candidate = node_candidate(selector, "semantic", node, analysis)
            if candidate:
                candidates.append(candidate)
    return candidates


def extractor_candidates(html: str, analysis: PageAnalysis) -> list[ExtractionCandidate]:
    candidates: list[ExtractionCandidate] = []
    extracted = trafilatura.extract(
        html,
        url=analysis.url,
        include_links=True,
        include_tables=True,
        include_images=True,
        include_formatting=True,
        output_format="html",
        favor_recall=True,
    )
    if extracted:
        node = BeautifulSoup(extracted, "html.parser")
        candidate = node_candidate("trafilatura", "trafilatura", node, analysis)
        if candidate:
            candidates.append(candidate)

    if ReadabilityDocument is not None:
        try:
            document = ReadabilityDocument(html)
            summary = document.summary(html_partial=True)
            if summary:
                node = BeautifulSoup(summary, "html.parser")
                candidate = node_candidate("readability", "readability", node, analysis)
                if candidate:
                    candidates.append(candidate)
        except Exception:
            pass
    return candidates


def jsonld_candidates(soup: BeautifulSoup, analysis: PageAnalysis) -> list[ExtractionCandidate]:
    candidates: list[ExtractionCandidate] = []
    items = analysis.metadata.get("jsonld", [])
    if not isinstance(items, list):
        return candidates
    for index, item in enumerate(items[:10]):
        if not isinstance(item, dict):
            continue
        item_types = normalize_jsonld_types(json_ld_type(item))
        if not item_types.intersection(ARTICLE_JSONLD_TYPES):
            continue
        markdown = markdown_from_jsonld(item)
        if not markdown:
            continue
        node = BeautifulSoup(markdown_to_simple_html(markdown), "html.parser")
        candidate = node_candidate(f"jsonld:{index}", "jsonld", node, analysis, markdown=markdown)
        if candidate:
            candidates.append(candidate)
    return candidates


def heuristic_candidates(soup: BeautifulSoup, analysis: PageAnalysis) -> list[ExtractionCandidate]:
    body = soup.body or soup
    candidates: list[ExtractionCandidate] = []
    for index, node in enumerate(body.find_all(["article", "main", "section", "div"], recursive=True)[:400]):
        if len(visible_text(node)) < MIN_MEANINGFUL_TEXT_LENGTH:
            continue
        if is_noisy_node(node):
            continue
        candidate = node_candidate(f"heuristic:{node.name}:{index}", "heuristic", node, analysis)
        if candidate:
            candidates.append(candidate)
    return candidates


def body_fallback_candidate(soup: BeautifulSoup, analysis: PageAnalysis) -> ExtractionCandidate | None:
    body = soup.body or soup
    return node_candidate("body-fallback", "heuristic", body, analysis)


def node_candidate(
    name: str,
    source: str,
    node,
    analysis: PageAnalysis,
    markdown: str | None = None,
) -> ExtractionCandidate | None:
    cloned = deepcopy(node)
    clean_content_node(cloned)
    normalize_links(cloned, analysis.url)
    text = visible_text(cloned)
    if len(text) < 80 and not cloned.find(["table", "pre", "code", "img"]):
        return None
    body = markdown if markdown is not None else markdown_from_node(cloned)
    body = clean_markdown(body, analysis.title)
    candidate = ExtractionCandidate(
        name=name,
        source=source,
        node=cloned,
        markdown=body,
        metadata={"base_url": analysis.url},
        dom_depth=node_depth(node),
    )
    return score_candidate(candidate, analysis)


def add_candidate(
    candidates: list[ExtractionCandidate],
    seen: set[str],
    candidate: ExtractionCandidate | None,
    analysis: PageAnalysis,
) -> None:
    if not candidate or candidate.hard_rejected:
        return
    key = candidate_fingerprint(candidate)
    if key in seen:
        return
    seen.add(key)
    if analysis.blocked_reason:
        candidate.quality_status = "blocked"
        candidate.quality_reasons = [analysis.blocked_reason]
    candidates.append(candidate)


def candidate_fingerprint(candidate: ExtractionCandidate) -> str:
    text = normalize_text(candidate.markdown)
    return re.sub(r"\W+", "", text[:500], flags=re.UNICODE).lower()


def normalize_jsonld_types(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip().lower() for part in re.split(r"[,/ ]+", value) if part.strip()}


def markdown_from_jsonld(item: dict) -> str:
    title = json_ld_scalar(item.get("headline")) or json_ld_scalar(item.get("name"))
    body = (
        json_ld_scalar(item.get("articleBody"))
        or json_ld_scalar(item.get("description"))
        or json_ld_scalar(item.get("text"))
    )
    author = json_ld_scalar(item.get("author"))
    date = json_ld_scalar(item.get("datePublished")) or json_ld_scalar(item.get("dateCreated"))
    if not body and not title:
        return ""
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
    metadata = []
    if author:
        metadata.append(f"- 作者：{author}")
    if date:
        metadata.append(f"- 发布时间：{date}")
    if metadata:
        lines.append("## 元数据\n\n" + "\n".join(metadata))
    if body:
        lines.append("## 正文\n\n" + body)
    return "\n\n".join(lines).strip()


def markdown_to_simple_html(markdown: str) -> str:
    html_lines: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            html_lines.append(f"<h{level}>{escape(heading.group(2))}</h{level}>")
        elif stripped.startswith("- "):
            html_lines.append(f"<li>{escape(stripped[2:])}</li>")
        else:
            html_lines.append(f"<p>{escape(stripped)}</p>")
    return "\n".join(html_lines)


def node_depth(node) -> int:
    depth = 0
    parent = getattr(node, "parent", None)
    while parent is not None:
        depth += 1
        parent = getattr(parent, "parent", None)
    return depth
