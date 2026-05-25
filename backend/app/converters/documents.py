import csv
import shutil
from pathlib import Path

from bs4 import BeautifulSoup
from docx import Document
from markdownify import markdownify as md
from pypdf import PdfReader

from app.converters.base import ConversionResult


def convert_text(path: Path, source_type: str = "text") -> ConversionResult:
    text = path.read_text(encoding="utf-8", errors="ignore")
    title = path.stem or "文本内容"
    return ConversionResult(title=title, source_type=source_type, body=text, summary_seed=text)


def convert_html(path: Path) -> ConversionResult:
    html = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "aside"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else path.stem
    body = md(str(soup.body or soup), heading_style="ATX")
    return ConversionResult(title=title, source_type="html", body=body, summary_seed=body)


def convert_csv(path: Path) -> ConversionResult:
    rows: list[list[str]] = []
    with path.open("r", encoding="utf-8-sig", newline="", errors="ignore") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    if not rows:
        body = ""
    else:
        header = rows[0]
        body_lines = [
            "| " + " | ".join(cell.strip() for cell in header) + " |",
            "| " + " | ".join("---" for _ in header) + " |",
        ]
        for row in rows[1:]:
            padded = row + [""] * (len(header) - len(row))
            body_lines.append("| " + " | ".join(cell.strip() for cell in padded[: len(header)]) + " |")
        body = "\n".join(body_lines)
    return ConversionResult(title=path.stem, source_type="csv", body=body, summary_seed=body)


def convert_pdf(path: Path) -> ConversionResult:
    reader = PdfReader(str(path))
    parts = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            parts.append(f"## Page {index}\n\n{text.strip()}")
    body = "\n\n".join(parts)
    return ConversionResult(title=path.stem, source_type="pdf", body=body, summary_seed=body)


def convert_docx(path: Path, assets_dir: Path) -> ConversionResult:
    document = Document(str(path))
    parts: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = paragraph.style.name.lower() if paragraph.style else ""
        if "heading 1" in style:
            parts.append(f"# {text}")
        elif "heading 2" in style:
            parts.append(f"## {text}")
        elif "heading 3" in style:
            parts.append(f"### {text}")
        else:
            parts.append(text)
    for table in document.tables:
        rows = [[cell.text.strip().replace("\n", " ") for cell in row.cells] for row in table.rows]
        if rows:
            width = max(len(row) for row in rows)
            header = rows[0] + [""] * (width - len(rows[0]))
            parts.append("| " + " | ".join(header) + " |")
            parts.append("| " + " | ".join("---" for _ in header) + " |")
            for row in rows[1:]:
                padded = row + [""] * (width - len(row))
                parts.append("| " + " | ".join(padded) + " |")

    resources: list[str] = []
    for rel in document.part.rels.values():
        if "image" not in rel.reltype:
            continue
        image_part = rel.target_part
        filename = Path(image_part.partname).name
        destination = assets_dir / filename
        destination.write_bytes(image_part.blob)
        resources.append(f"- 图片资源：./assets/{filename}")

    body = "\n\n".join(parts)
    return ConversionResult(title=path.stem, source_type="docx", body=body, summary_seed=body, resources=resources)


def convert_by_extension(path: Path, assets_dir: Path) -> ConversionResult:
    extension = path.suffix.lower()
    if extension in {".txt", ".md"}:
        return convert_text(path)
    if extension in {".html", ".htm"}:
        return convert_html(path)
    if extension == ".csv":
        return convert_csv(path)
    if extension == ".pdf":
        return convert_pdf(path)
    if extension == ".docx":
        return convert_docx(path, assets_dir)
    raise ValueError(f"Unsupported document type: {extension}")

