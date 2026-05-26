from pathlib import Path

from docx import Document

from app.converters.documents import convert_csv, convert_text
from app.converters.documents import convert_docx


def test_convert_text(tmp_path: Path):
    path = tmp_path / "note.txt"
    path.write_text("Hello\nWorld", encoding="utf-8")
    result = convert_text(path)
    assert result.title == "note"
    assert "Hello" in result.body


def test_convert_csv(tmp_path: Path):
    path = tmp_path / "data.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    result = convert_csv(path)
    assert "| a | b |" in result.body
    assert "| 1 | 2 |" in result.body


def test_convert_docx_with_heading_table_and_image_dir(tmp_path: Path):
    path = tmp_path / "report.docx"
    document = Document()
    document.add_heading("Report Title", level=1)
    document.add_paragraph("A useful paragraph.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Name"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Alpha"
    table.cell(1, 1).text = "42"
    document.save(path)

    result = convert_docx(path, tmp_path / "assets")

    assert "# Report Title" in result.body
    assert "A useful paragraph." in result.body
    assert "| Name | Value |" in result.body
    assert "| Alpha | 42 |" in result.body
