# Web Extractors

MarkdownEverything keeps webpage conversion extensible through small extractor modules.

The core converter still handles fetching, SSRF checks, browser rendering, generic article extraction, image downloads, and final Markdown packaging. Site-specific or page-type-specific logic should live in `backend/app/converters/web_extractors/`.

## Built-In Extractors

- `bilibili.py`: extracts the Bilibili homepage recommendation feed into a clean video table, and extracts Bilibili video detail pages into video metadata, author, stats, description, tags, cover, and source link.
- `discourse.py`: extracts Discourse forum topics into topic metadata, post index, and per-post Markdown.
- `github.py`: extracts public GitHub pull request pages into repository metadata, PR number/title/author, PR description, visible comments, and visible commit summaries without GitHub header navigation noise.
- `nodeseek.py`: extracts NodeSeek forum posts into topic metadata, floor index, and per-floor Markdown without sidebar, login, and forum navigation noise.
- `snapshot.py`: fallback for dynamic app/home/search pages. It outputs page description, metadata, visible headings, controls, text blocks, lists, tables, links, images, and media URLs.
- `wikipedia.py`: extracts Wikipedia articles across languages from the parser output, preserving article text, headings, infoboxes, tables, and media while removing language switchers, edit controls, notices, references, and navigation boxes.

## Compatibility Benchmark

The repository includes a broad webpage benchmark corpus in `backend/benchmarks/web_100_sites.yml`. It currently covers 102 URLs across search, documentation, encyclopedia, news, blogs, repositories, package registries, forums, Q&A, social, video, ecommerce, product, marketing, education, government, finance, data, map, travel, design, and reference sites.

Run a quick smoke test:

```powershell
cd backend
$env:PYTHONIOENCODING="utf-8"
python benchmarks\run_web_compat.py --limit 10 --timeout 35 --concurrency 2
```

Run the full corpus:

```powershell
cd backend
$env:PYTHONIOENCODING="utf-8"
python benchmarks\run_web_compat.py --timeout 45 --concurrency 4 --retry-failed --retry-timeout 60 --retry-concurrency 1
```

The benchmark prints one line per completed site and continuously writes:

- `backend/benchmarks/results/web_compat_latest.json`
- `backend/benchmarks/results/web_compat_latest.md`

Benchmark results are intentionally ignored by git. Use them as a local quality gate and as a failure list when adding new extractors.

Failed pages are classified with reasons such as `network_or_timeout`, `login_or_blocked`, `challenge_or_js_required`, and `empty_or_blocked_response`. This keeps the product boundary clear: MarkdownEverything improves extraction and public-page compatibility, but it does not bypass login walls, paywalls, bot challenges, DRM, or private access controls.

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
