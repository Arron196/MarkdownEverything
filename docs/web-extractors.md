# Web Extractors

MarkdownEverything keeps webpage conversion extensible through small extractor modules.

The core converter still handles fetching, SSRF checks, browser rendering, generic article extraction, image downloads, and final Markdown packaging. Site-specific or page-type-specific logic should live in `backend/app/converters/web_extractors/`.

## Built-In Extractors

- `discourse.py`: extracts Discourse forum topics into topic metadata, post index, and per-post Markdown.
- `snapshot.py`: fallback for dynamic app/home/search pages. It outputs page description, visible headings, controls, text blocks, links, and images.

## Add A New Extractor

Create a module such as:

```python
# backend/app/converters/web_extractors/github_issue.py
from app.converters.web_extractors.base import WebExtractorContext, WebExtractorResult


def extract(context: WebExtractorContext) -> WebExtractorResult | None:
    if "github.com" not in context.base_url:
        return None

    issue_title = context.soup.select_one("bdi.js-issue-title")
    comments = context.soup.select(".js-comment-body")
    if not issue_title or not comments:
        return None

    body = "## Issue\n\n" + issue_title.get_text(" ", strip=True)
    return WebExtractorResult(name="github-issue", body=body, score=800)
```

Then register it in `backend/app/converters/web_extractors/registry.py`:

```python
from app.converters.web_extractors import discourse, github_issue

SPECIALIZED_EXTRACTORS = [
    discourse.extract,
    github_issue.extract,
]
```

## Extractor Contract

An extractor receives `WebExtractorContext`:

- `soup`: BeautifulSoup for the fetched or rendered page.
- `base_url`: final URL after redirects/rendering.
- `title`: normalized page title.
- `rendered`: whether the page came from browser rendering.
- `metadata`: core extraction hints, such as generic candidate score.

Return `None` when the extractor does not apply.

Return `WebExtractorResult` when it applies:

- `name`: stable extractor name for logs/metadata.
- `body`: Markdown body without frontmatter.
- `score`: higher wins when multiple specialized extractors match.
- `metadata`: optional details for debugging.

## Guidelines

- Keep extractors narrow and predictable.
- Preserve source links with absolute URLs.
- Remove UI chrome, buttons, reaction bars, share menus, and login prompts.
- Preserve headings, paragraphs, code blocks, tables, quotes, image alt text, authors, timestamps, and canonical links when available.
- Add tests in `backend/tests/test_web_converter.py` or a dedicated test file.
- Do not bypass login, paywalls, DRM, or private access controls.
