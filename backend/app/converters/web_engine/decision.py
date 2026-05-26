from app.config import settings
from app.converters.web_engine.models import ExtractionCandidate, PageAnalysis
from app.converters.web_engine.scoring import SOURCE_PRIORITY

QUALITY_RANK = {
    "rejected": 0,
    "blocked": 1,
    "weak": 2,
    "usable": 3,
    "strong": 4,
}


def select_winner(candidates: list[ExtractionCandidate], analysis: PageAnalysis) -> ExtractionCandidate | None:
    viable = [candidate for candidate in candidates if not candidate.hard_rejected]
    if not viable:
        return None
    high_confidence_specialized = [
        candidate
        for candidate in viable
        if candidate.source == "specialized" and candidate.source_score >= 900 and candidate.markdown.strip()
    ]
    if high_confidence_specialized:
        return max(high_confidence_specialized, key=lambda candidate: candidate.source_score)
    if analysis.blocked_reason:
        return max(viable, key=lambda candidate: candidate.metrics.L)
    page_snapshot = best_page_snapshot(viable, analysis)
    if page_snapshot is not None:
        return page_snapshot
    return max(viable, key=tie_break_key)


def should_render_static_page(
    best_static: ExtractionCandidate | None,
    analysis: PageAnalysis,
    render_available: bool,
    fetch_error_renderable: bool = False,
) -> bool:
    if not settings.web_render_enabled or not render_available:
        return False
    if fetch_error_renderable:
        return True
    if analysis.blocked_reason in {"access_denied", "captcha", "login"}:
        return False
    if analysis.blocked_reason and analysis.blocked_reason == "js_required":
        return True
    if best_static and best_static.quality_status == "strong" and best_static.score >= 85 and not analysis.blocked_reason:
        return False
    if best_static is None:
        return True
    if best_static.quality_status in {"weak", "blocked"}:
        return True
    if analysis.visible_text_length < 120:
        return True
    if analysis.dom_has_spa_signature:
        return True
    return False


def top_candidate_metadata(candidates: list[ExtractionCandidate], limit: int = 5) -> list[dict]:
    return [
        {
            "name": candidate.name,
            "source": candidate.source,
            "score": round(candidate.score, 2),
            "quality": candidate.quality_status,
        }
        for candidate in sorted(candidates, key=lambda item: item.score, reverse=True)[:limit]
        if not candidate.hard_rejected
    ]


def best_page_snapshot(candidates: list[ExtractionCandidate], analysis: PageAnalysis) -> ExtractionCandidate | None:
    if analysis.page_kind not in {"home", "search", "list"} and not analysis.dom_has_spa_signature:
        return None
    snapshots = [candidate for candidate in candidates if candidate.source == "snapshot"]
    if not snapshots:
        return None
    generic_article = [
        candidate
        for candidate in candidates
        if candidate.source != "snapshot" and candidate.source != "specialized"
    ]
    if any(candidate.quality_status in {"strong", "usable"} and candidate.score >= 85 for candidate in generic_article):
        return None
    snapshot = max(snapshots, key=lambda candidate: (candidate.metrics.L, candidate.score))
    if snapshot.metrics.L >= 120:
        snapshot.quality_status = "usable" if analysis.page_kind in {"home", "search", "list"} else snapshot.quality_status
        if "page_snapshot" not in snapshot.quality_reasons:
            snapshot.quality_reasons.append("page_snapshot")
        snapshot.score = max(snapshot.score, min(75, 55 + snapshot.metrics.H + snapshot.metrics.LI + snapshot.metrics.IM))
        if analysis.dom_has_spa_signature and snapshot.metrics.L >= 250 and snapshot.quality_status == "weak":
            snapshot.quality_status = "usable"
    return snapshot


def tie_break_key(candidate: ExtractionCandidate):
    return (
        QUALITY_RANK.get(candidate.quality_status, 0),
        candidate.score,
        -candidate.metrics.noise_cost,
        candidate.metrics.P + candidate.metrics.H + candidate.metrics.TB + candidate.metrics.CB,
        SOURCE_PRIORITY.get(candidate.source, 0),
        -candidate.dom_depth,
    )
