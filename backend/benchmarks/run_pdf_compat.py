import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.converters.documents import convert_pdf  # noqa: E402
from app.markdown import render_document_markdown  # noqa: E402
from app.quality import score_conversion  # noqa: E402


def download(url: str, destination: Path) -> None:
    if destination.exists():
        return
    request = Request(url, headers={"User-Agent": "MarkdownEverything PDF benchmark/1.0"})
    with urlopen(request, timeout=120) as response:
        destination.write_bytes(response.read())


def check_expectations(row: dict, expected: dict) -> list[str]:
    failures: list[str] = []
    checks = [
        ("min_pages", "pages"),
        ("min_formulas", "formulas"),
        ("min_inline_images", "inline_images"),
        ("min_tables", "tables"),
    ]
    for expected_key, row_key in checks:
        if expected_key in expected and row.get(row_key, 0) < expected[expected_key]:
            failures.append(f"{row_key}<{expected[expected_key]}")
    title_contains = expected.get("title_contains")
    if title_contains and title_contains.lower() not in row.get("title", "").lower():
        failures.append(f"title_missing:{title_contains}")
    return failures


def run(config_path: Path, output_dir: Path) -> list[dict]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    rows: list[dict] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for sample in config["samples"]:
        sample_dir = output_dir / sample["id"]
        assets_dir = sample_dir / "assets"
        sample_dir.mkdir(parents=True, exist_ok=True)
        assets_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = sample_dir / f"{sample['id']}.pdf"
        row = {"id": sample["id"], "url": sample["url"]}
        try:
            download(sample["url"], pdf_path)
            result = convert_pdf(pdf_path, assets_dir)
            result.metadata.update(score_conversion(result))
            markdown = render_document_markdown(
                title=result.title,
                source_type=result.source_type,
                body=result.body,
                summary="PDF benchmark run.",
                source_url=result.source_url,
                author=result.author,
                created_at=result.created_at,
                resources=result.resources,
            )
            (sample_dir / "result.md").write_text(markdown, encoding="utf-8")
            row.update(
                {
                    "title": result.title,
                    "pages": result.metadata.get("page_count", 0),
                    "characters": result.metadata.get("character_count", 0),
                    "headings": result.metadata.get("heading_count", 0),
                    "tables": result.metadata.get("table_count", 0),
                    "formulas": result.metadata.get("formula_count", 0),
                    "inline_images": markdown.count("!["),
                    "assets": len(list(assets_dir.glob("*"))),
                    "quality_score": result.metadata.get("quality_score"),
                    "quality_status": result.metadata.get("quality_status"),
                    "quality_warnings": result.metadata.get("quality_warnings"),
                    "result": str(sample_dir / "result.md"),
                }
            )
            row["failures"] = check_expectations(row, sample.get("expected", {}))
        except Exception as exc:
            row["error"] = str(exc)
            row["failures"] = ["exception"]
        rows.append(row)
    (output_dir / "report.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "benchmarks" / "pdf_english.yml")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "benchmarks" / "pdf_english")
    args = parser.parse_args()
    rows = run(args.config, args.output)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 1 if any(row.get("failures") for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
