from app.converters.base import ConversionResult
from app.quality import score_conversion


def test_quality_scores_structured_pdf_as_strong_or_usable():
    result = ConversionResult(
        title="Research Report",
        source_type="pdf",
        body=(
            "## Introduction\n\n"
            + "\n\n".join("This is a useful paragraph with enough text for scoring. " * 8 for _ in range(8))
            + "\n\n| Name | Value |\n| --- | --- |\n| Alpha | 42 |\n\n"
            + "![figure](./assets/figure.png)\n\n"
            + "$$\ny = mx + b\n$$"
        ),
        resources=["- 图片资源：./assets/figure.png"],
        metadata={"pdf_engine": "pymupdf", "table_count": 1, "image_count": 1, "formula_count": 1},
    )

    quality = score_conversion(result)

    assert quality["quality_score"] >= 55
    assert quality["quality_status"] in {"usable", "strong"}
    assert "tables_detected" in quality["quality_reasons"]
    assert "images_detected" in quality["quality_reasons"]
    assert quality["quality_metrics"]["table_count"] == 1


def test_quality_warns_for_empty_document():
    result = ConversionResult(title="empty", source_type="pdf", body="", metadata={"warning": "No extractable text found."})

    quality = score_conversion(result)

    assert quality["quality_status"] == "failed"
    assert "no_extractable_content" in quality["quality_warnings"]
    assert "converter_warning" in quality["quality_warnings"]
