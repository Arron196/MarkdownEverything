import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from markdownify import markdownify as md


def markdown_from_node(node) -> str:
    return md(
        str(node),
        heading_style="ATX",
        bullets="-",
        strip=["script", "style"],
    )


def normalize_links(node, base_url: str) -> None:
    for anchor in list(node.find_all("a")):
        href = anchor.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        href = href.strip()
        parsed = urlparse(href)
        if href.startswith("#") or parsed.scheme in {"mailto", "tel"}:
            anchor["href"] = href
            continue
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            anchor.unwrap()
            continue
        anchor["href"] = urljoin(base_url, href)


def visible_text(node) -> str:
    return normalize_text(node.get_text(" ", strip=True))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def unique_preserve_order(values) -> list:
    result = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def clean_markdown(body: str, title: str = "") -> str:
    text = body.replace("\u200b", "").replace("\xa0", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"(?m)^(#{1,6})\s*\n+([^#`\n][^\n]{0,140})\s*$", r"\1 \2", text)
    text = re.sub(
        r"(?is)(?:^|\n)>?\s*#{1,6}\s*Documentation Index\b.{0,1200}?Use this file to discover all available pages before exploring further\.?",
        "\n",
        text,
    )
    text = re.sub(
        r"(?im)^\s*(跳转到主要内容|Skip to main content|OpenAI 在 ChatGPT 中打开|Open in ChatGPT)\s*$\n?",
        "",
        text,
    )
    text = remove_duplicate_title(text, title)
    text = re.sub(r"(?m)^\s*#{1,6}\s*$\n?", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\n(#{2,6} )", r"\n\n\1", text)
    text = re.sub(r"\n(```)", r"\n\n\1", text)
    text = re.sub(r"```\n{2,}", "```\n", text)
    text = re.sub(r"\n{2,}```", "\n\n```", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def remove_duplicate_title(text: str, title: str) -> str:
    if not title.strip():
        return text
    title_key = comparable_heading(title)
    lines = text.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        return text

    first = lines[index].strip()
    heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", first)
    first_text = heading_match.group(1) if heading_match else first
    if comparable_heading(first_text) != title_key:
        return text

    del lines[index]
    while index < len(lines) and not lines[index].strip():
        del lines[index]
    return "\n".join(lines)


def comparable_heading(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()


def soup_from_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def extract_image_source(img, base_url: str) -> str | None:
    image_source_attrs = [
        "src",
        "data-src",
        "data-original",
        "data-lazy-src",
        "data-image",
        "data-url",
    ]
    image_srcset_attrs = ["srcset", "data-srcset"]
    candidates: list[str] = []
    for attr in image_source_attrs:
        value = img.get(attr)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    for attr in image_srcset_attrs:
        value = img.get(attr)
        if isinstance(value, str) and value.strip():
            srcset_url = best_srcset_url(value)
            if srcset_url:
                candidates.append(srcset_url)

    for candidate in candidates:
        if candidate.startswith(("data:", "blob:", "javascript:")):
            continue
        absolute = urljoin(base_url, candidate)
        if urlparse(absolute).scheme in {"http", "https"}:
            return absolute
    return None


def best_srcset_url(srcset: str) -> str | None:
    best_url: str | None = None
    best_score = -1.0
    for raw_entry in srcset.split(","):
        parts = raw_entry.strip().split()
        if not parts:
            continue
        url = parts[0]
        descriptor = parts[1] if len(parts) > 1 else ""
        score = 1.0
        if descriptor.endswith("w"):
            score = float(descriptor[:-1] or 0)
        elif descriptor.endswith("x"):
            score = float(descriptor[:-1] or 1) * 1000
        if score > best_score:
            best_url = url
            best_score = score
    return best_url


def is_placeholder_image(img, image_url: str) -> bool:
    path = urlparse(image_url).path.lower()
    if re.search(r"(spacer|pixel|tracking|transparent|blank|placeholder)", path):
        return True
    try:
        width = int(str(img.get("width", "0")).replace("px", "") or 0)
        height = int(str(img.get("height", "0")).replace("px", "") or 0)
        return 0 < width <= 2 and 0 < height <= 2
    except ValueError:
        return False


def image_label_from_url(image_url: str) -> str:
    return Path(urlparse(image_url).path).name or "image"
