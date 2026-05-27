from datetime import datetime, timezone
from typing import Any

import yaml


def frontmatter(metadata: dict[str, Any]) -> str:
    clean = {key: value for key, value in metadata.items() if value not in (None, "", [])}
    return "---\n" + yaml.safe_dump(clean, allow_unicode=True, sort_keys=False).strip() + "\n---"


def render_document_markdown(
    *,
    title: str,
    source_type: str,
    body: str,
    summary: str,
    source_url: str | None = None,
    author: str | None = None,
    created_at: str | None = None,
    resources: list[str] | None = None,
) -> str:
    metadata = {
        "title": title,
        "source_type": source_type,
        "source_url": source_url,
        "author": author,
        "created_at": created_at,
        "converted_at": datetime.now(timezone.utc).isoformat(),
        "tags": [],
        "assets_dir": "./assets",
    }
    resource_lines = list(resources or [])
    if source_url:
        resource_lines.insert(0, f"- 原始链接：{source_url}")
    resources_md = "\n".join(resource_lines) if resource_lines else "- 图片资源：\n- 附件："
    return f"""{frontmatter(metadata)}

# {title}

## 摘要

{summary.strip() or "暂无摘要。"}

## 正文

{body.strip() or "未提取到正文内容。"}

## 资源

{resources_md}
"""


def render_media_markdown(
    *,
    title: str,
    source_type: str,
    timeline: list[dict[str, str]],
    summary: str,
    source_url: str | None = None,
    duration: str | None = None,
    language: str | None = None,
) -> str:
    metadata = {
        "title": title,
        "source_type": source_type,
        "source_url": source_url,
        "duration": duration,
        "language": language,
        "converted_at": datetime.now(timezone.utc).isoformat(),
        "assets_dir": "./assets",
    }
    timeline_blocks = []
    for segment in timeline:
        start = segment.get("start", "00:00")
        end = segment.get("end", "")
        heading = segment.get("title") or "转写片段"
        text = segment.get("text", "").strip()
        timeline_blocks.append(f"### {start} - {end} {heading}\n\n{text}")
    return f"""{frontmatter(metadata)}

# {title}

## 摘要

{summary.strip() or "暂无摘要。"}

## 时间轴

{chr(10).join(timeline_blocks) if timeline_blocks else "未生成转写时间轴。"}
"""
