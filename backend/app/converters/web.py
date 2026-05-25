import mimetypes
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup
from fastapi import HTTPException, status
from markdownify import markdownify as md

from app.config import settings
from app.converters.base import ConversionResult
from app.services.url_security import assert_public_url


async def convert_webpage(url: str, assets_dir: Path) -> ConversionResult:
    assert_public_url(url)
    limits = httpx.Limits(max_connections=4)
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        follow_redirects=True,
        max_redirects=settings.redirect_limit,
        limits=limits,
        headers={"User-Agent": "MarkdownEverything/1.0"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL does not look like an HTML page")
        if len(response.content) > settings.max_web_response_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Web response is too large")
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "aside", "form"]):
        tag.decompose()

    title = metadata_value(soup, "og:title") or (soup.title.get_text(strip=True) if soup.title else None) or urlparse(url).netloc
    author = metadata_value(soup, "author") or metadata_value(soup, "article:author")
    created_at = metadata_value(soup, "article:published_time") or metadata_value(soup, "date")

    extracted = trafilatura.extract(html, include_links=True, include_tables=True, output_format="markdown")
    body = extracted or md(str(soup.body or soup), heading_style="ATX")
    resources = await download_images(soup, url, assets_dir)
    return ConversionResult(
        title=title.strip(),
        source_type="webpage",
        body=body.strip(),
        summary_seed=body,
        source_url=url,
        author=author,
        created_at=created_at,
        resources=resources,
    )


def metadata_value(soup: BeautifulSoup, key: str) -> str | None:
    tag = soup.find("meta", attrs={"name": key}) or soup.find("meta", attrs={"property": key})
    if not tag:
        return None
    content = tag.get("content")
    return content.strip() if isinstance(content, str) else None


async def download_images(soup: BeautifulSoup, base_url: str, assets_dir: Path) -> list[str]:
    resources: list[str] = []
    images = soup.find_all("img")[: settings.max_images_per_job]
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, follow_redirects=True, max_redirects=2) as client:
        for index, img in enumerate(images, start=1):
            src = img.get("src")
            if not isinstance(src, str) or not src.strip():
                continue
            image_url = urljoin(base_url, src)
            try:
                assert_public_url(image_url)
                response = await client.get(image_url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0]
                if not content_type.startswith("image/"):
                    continue
                extension = mimetypes.guess_extension(content_type) or Path(urlparse(image_url).path).suffix or ".img"
                filename = f"image-{index}{extension}"
                (assets_dir / filename).write_bytes(response.content)
                resources.append(f"- 图片资源：./assets/{filename}")
            except Exception:
                continue
    return resources

