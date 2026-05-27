import re

from app.converters.base import ConversionResult


def score_conversion(result: ConversionResult) -> dict:
    text = markdown_visible_text(result.body)
    text_length = len(text)
    paragraphs = count_paragraphs(result.body)
    headings = count_headings(result.body)
    tables = int(result.metadata.get("table_count") or count_tables(result.body))
    images = max(int(result.metadata.get("image_count") or 0), count_images(result.body), len(result.resources))
    formulas = int(result.metadata.get("formula_count") or count_formulas(result.body))
    code_blocks = result.body.count("```") // 2
    timeline_segments = len(result.timeline)
    warnings = quality_warnings(result, text_length, headings, paragraphs, tables, images, formulas, timeline_segments)

    if result.timeline:
        score = score_media_result(timeline_segments, text_length, result)
    else:
        score = score_document_result(text_length, paragraphs, headings, tables, images, formulas, code_blocks, result)

    if warnings:
        score -= min(35, 10 + len(warnings) * 8)
    score = max(0, min(100, round(score, 1)))
    status = quality_status(score, warnings)
    reasons = quality_reasons(result, text_length, paragraphs, headings, tables, images, formulas, code_blocks, timeline_segments)

    return {
        "quality_score": score,
        "quality_status": status,
        "quality_reasons": reasons,
        "quality_warnings": warnings,
        "quality_metrics": {
            "text_length": text_length,
            "paragraph_count": paragraphs,
            "heading_count": headings,
            "table_count": tables,
            "image_count": images,
            "formula_count": formulas,
            "code_block_count": code_blocks,
            "timeline_segment_count": timeline_segments,
        },
    }


def score_document_result(
    text_length: int,
    paragraphs: int,
    headings: int,
    tables: int,
    images: int,
    formulas: int,
    code_blocks: int,
    result: ConversionResult,
) -> float:
    score = 0.0
    score += min(text_length / 160, 30)
    score += min(paragraphs * 1.2, 18)
    score += min(headings * 2.2, 14)
    score += min(tables * 4.0, 12)
    score += min(images * 2.0, 10)
    score += min(formulas * 1.8, 8)
    score += min(code_blocks * 2.5, 8)
    if is_informative_title(result.title):
        score += 6
    if result.source_url:
        score += 3
    if result.metadata.get("pdf_engine") == "pymupdf":
        score += 4
    return score


def score_media_result(timeline_segments: int, text_length: int, result: ConversionResult) -> float:
    score = 0.0
    score += min(timeline_segments * 5.0, 40)
    score += min(text_length / 180, 30)
    if result.duration:
        score += 8
    if result.language:
        score += 6
    if is_informative_title(result.title):
        score += 6
    if result.source_url:
        score += 4
    return score


def quality_status(score: float, warnings: list[str]) -> str:
    if score < 25:
        return "failed" if "no_extractable_content" in warnings else "weak"
    if score >= 80 and not warnings:
        return "strong"
    if score >= 55:
        return "usable"
    return "weak"


def quality_reasons(
    result: ConversionResult,
    text_length: int,
    paragraphs: int,
    headings: int,
    tables: int,
    images: int,
    formulas: int,
    code_blocks: int,
    timeline_segments: int,
) -> list[str]:
    reasons: list[str] = []
    if text_length >= 1200:
        reasons.append("sufficient_text_volume")
    if paragraphs >= 8:
        reasons.append("paragraph_structure_detected")
    if headings >= 3:
        reasons.append("heading_hierarchy_detected")
    if tables:
        reasons.append("tables_detected")
    if images:
        reasons.append("images_detected")
    if formulas:
        reasons.append("formula_blocks_detected")
    if code_blocks:
        reasons.append("code_blocks_detected")
    if timeline_segments:
        reasons.append("timestamped_timeline_detected")
    if result.source_url:
        reasons.append("source_url_preserved")
    if is_informative_title(result.title):
        reasons.append("informative_title_detected")
    return reasons


def quality_warnings(
    result: ConversionResult,
    text_length: int,
    headings: int,
    paragraphs: int,
    tables: int,
    images: int,
    formulas: int,
    timeline_segments: int,
) -> list[str]:
    warnings: list[str] = []
    if result.metadata.get("warning"):
        warnings.append("converter_warning")
    if not result.timeline and text_length < 80 and not images and not tables:
        warnings.append("no_extractable_content")
    elif not result.timeline and text_length < 300:
        warnings.append("low_text_volume")
    if not result.timeline and text_length >= 1000 and headings == 0 and tables == 0:
        warnings.append("low_structure_detected")
    if result.timeline and timeline_segments == 0:
        warnings.append("missing_timeline")
    if formulas and result.metadata.get("pdf_formula_engine") == "symbolic_preserve":
        warnings.append("formula_text_preserved_not_ocr")
    if paragraphs > 200 and headings == 0:
        warnings.append("long_text_without_headings")
    return warnings


def markdown_visible_text(markdown: str) -> str:
    text = re.sub(r"```.*?```", " ", markdown, flags=re.S)
    text = re.sub(r"!\[[^\]]*]\([^)]+\)", " ", text)
    text = re.sub(r"\[[^\]]+]\([^)]+\)", lambda match: match.group(0).split("](")[0].lstrip("["), text)
    text = re.sub(r"[$#>*_`|\\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_informative_title(title: str) -> bool:
    normalized = re.sub(r"[\W_]+", "", title or "").lower()
    return len(normalized) >= 6 and normalized not in {"untitled", "document", "source", "upload"}


def count_paragraphs(markdown: str) -> int:
    return len([part for part in re.split(r"\n\s*\n", markdown.strip()) if markdown_visible_text(part)])


def count_headings(markdown: str) -> int:
    return len(re.findall(r"(?m)^#{1,6}\s+\S", markdown))


def count_tables(markdown: str) -> int:
    return len(re.findall(r"(?m)^\|.+\|\n\|(?:\s*:?-{3,}:?\s*\|)+", markdown))


def count_images(markdown: str) -> int:
    return len(re.findall(r"!\[[^\]]*]\([^)]+\)", markdown))


def count_formulas(markdown: str) -> int:
    return len(re.findall(r"(?s)\$\$\n.+?\n\$\$", markdown))
