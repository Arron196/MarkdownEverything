import re

from bs4 import Comment

from app.converters.web_extractors.utils import visible_text

MIN_MEANINGFUL_TEXT_LENGTH = 200

CONTENT_SELECTORS: list[tuple[str, float]] = [
    ("#content", 40),
    ("main article", 40),
    ("[role='main'] article", 40),
    ("article", 40),
    (".markdown-body", 40),
    (".mdx-content", 40),
    ("main .prose", 40),
    (".prose", 40),
    (".docs-content", 40),
    (".doc-content", 40),
    (".documentation", 40),
    ("[data-pagefind-body]", 40),
    ("[role='main']", 40),
    ("main", 40),
    (".content", 12),
]

NOISE_SELECTORS = (
    "script, style, noscript, template, nav, footer, aside, form, button, input, select, textarea, "
    "iframe, object, embed, canvas, svg, dialog, "
    "[hidden], [aria-hidden='true'], [style*='display:none'], [style*='display: none'], "
    "[style*='visibility:hidden'], [style*='visibility: hidden'], [data-agent-docs-index], "
    "[role='navigation'], [role='search'], [role='complementary'], [role='banner'], "
    "[aria-label='导航到标题'], [aria-label='Navigate to heading'], "
    "#table-of-contents-content, #toc, .toc, .table-of-contents, .sidebar, .breadcrumbs, "
    ".breadcrumb, .sr-only, .visually-hidden, .screen-reader-text, "
    "[class*='TableOfContents'], [class*='table-of-contents'], [class*='toc'], [id*='toc'], "
    "[class*='sidebar'], [id*='sidebar'], [class*='breadcrumb'], [id*='breadcrumb'], "
    "[class*='navbar'], [class*='navigation'], [id*='navigation'], "
    "[class*='pagination'], [class*='pager'], [class*='share'], [class*='social'], "
    "[class*='advert'], [class*='cookie'], [class*='newsletter'], [class*='subscribe']"
)

NOISE_TEXT_PATTERNS = [
    re.compile(r"^skip to main content$", re.I),
    re.compile(r"^跳转到主要内容$"),
    re.compile(r"^(open in chatgpt|openai\s+在\s+chatgpt\s+中打开)$", re.I),
]

NOISE_ATTR_PATTERN = re.compile(
    r"(nav|sidebar|toc|breadcrumb|footer|header|menu|share|social|cookie|advert|newsletter|"
    r"subscribe|login|signin|signup|modal|drawer|popover)",
    re.I,
)


def clean_content_node(node) -> None:
    for comment in node.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()

    for tag in list(node.select(NOISE_SELECTORS)):
        if tag.parent is not None:
            tag.decompose()

    for tag in list(node.find_all(["blockquote", "section", "div", "p", "a", "button"])):
        if tag.parent is None:
            continue
        text = visible_text(tag)
        if is_agent_docs_block(text):
            tag.decompose()
            continue
        if len(text) <= 80 and any(pattern.search(text) for pattern in NOISE_TEXT_PATTERNS):
            tag.decompose()

    for anchor in list(node.find_all("a")):
        if anchor.parent is None:
            continue
        href = anchor.get("href", "")
        text = anchor.get_text(" ", strip=True)
        if isinstance(href, str) and href.startswith("#") and not text:
            anchor.decompose()
        elif not text and not anchor.find("img"):
            anchor.decompose()

    for tag in list(node.find_all(True)):
        if tag.parent is None or tag.attrs is None:
            continue
        if tag.name in {"p", "li", "span", "div"} and not visible_text(tag) and not tag.find(["img", "pre", "code", "table"]):
            tag.decompose()
            continue
        allowed_attrs = {
            "href",
            "src",
            "alt",
            "title",
            "datetime",
            "srcset",
            "data-src",
            "data-srcset",
            "data-original",
            "data-lazy-src",
            "data-image",
            "data-url",
            "width",
            "height",
            "colspan",
            "rowspan",
        }
        tag.attrs = {key: value for key, value in tag.attrs.items() if key in allowed_attrs}


def is_noisy_node(node) -> bool:
    attrs = " ".join(
        str(value).lower()
        for key, value in getattr(node, "attrs", {}).items()
        if key in {"id", "class", "role", "aria-label"}
    )
    return bool(NOISE_ATTR_PATTERN.search(attrs))


def is_agent_docs_block(text: str) -> bool:
    lowered = text.lower()
    return "documentation index" in lowered and "llms.txt" in lowered


def meaningful_body(body: str) -> bool:
    text = re.sub(r"[\s#`|\\-]+", "", body)
    return len(text) > MIN_MEANINGFUL_TEXT_LENGTH

