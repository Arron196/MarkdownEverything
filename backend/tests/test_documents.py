from pathlib import Path

from app.converters.documents import convert_csv, convert_text


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

