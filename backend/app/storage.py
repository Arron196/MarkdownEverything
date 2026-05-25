import json
import shutil
import zipfile
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException, UploadFile, status

from app.config import settings


def job_root(job_id: str) -> Path:
    return settings.storage_root / "jobs" / job_id


def ensure_job_dirs(job_id: str) -> dict[str, Path]:
    root = job_root(job_id)
    paths = {
        "root": root,
        "input": root / "input",
        "output": root / "output",
        "assets": root / "assets",
        "logs": root / "logs",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def remove_job_dir(job_id: str) -> None:
    shutil.rmtree(job_root(job_id), ignore_errors=True)


async def save_upload(job_id: str, upload: UploadFile) -> tuple[Path, int]:
    paths = ensure_job_dirs(job_id)
    safe_name = Path(upload.filename or "upload.bin").name
    destination = paths["input"] / safe_name
    size = 0
    with destination.open("wb") as out:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Upload exceeds {settings.max_upload_mb} MB",
                )
            out.write(chunk)
    return destination, size


def write_text_input(job_id: str, filename: str, content: str) -> Path:
    paths = ensure_job_dirs(job_id)
    destination = paths["input"] / filename
    destination.write_text(content, encoding="utf-8")
    return destination


def write_result(job_id: str, markdown: str, metadata: dict) -> tuple[Path, Path]:
    paths = ensure_job_dirs(job_id)
    md_path = paths["output"] / "result.md"
    metadata_path = paths["output"] / "metadata.json"
    md_path.write_text(markdown, encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    zip_path = paths["output"] / "result.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(md_path, "result.md")
        archive.write(metadata_path, "metadata.json")
        assets_dir = paths["assets"]
        if assets_dir.exists():
            for asset in assets_dir.rglob("*"):
                if asset.is_file():
                    archive.write(asset, f"assets/{asset.relative_to(assets_dir).as_posix()}")
    return md_path, zip_path


def append_job_log_file(job_id: str, message: str) -> None:
    paths = ensure_job_dirs(job_id)
    log_file = paths["logs"] / "worker.log"
    with log_file.open("a", encoding="utf-8") as out:
        out.write(message.rstrip() + "\n")


def storage_usage_bytes() -> int:
    root = settings.storage_root
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())

