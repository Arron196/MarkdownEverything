import re
from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.converters.web_extractors.base import WebExtractorContext, WebExtractorResult
from app.converters.web_extractors.utils import clean_markdown, markdown_from_node, normalize_links, normalize_text


@dataclass
class GitHubPullRequest:
    owner: str
    repo: str
    number: str
    title: str
    author: str | None
    url: str
    description: str | None
    comments: list[dict[str, str]]
    commits: list[str]


def extract(context: WebExtractorContext) -> WebExtractorResult | None:
    if not is_github_pull_request(context.base_url):
        return None
    pull_request = extract_pull_request(context.soup, context.base_url)
    if not pull_request:
        return None
    body = render_pull_request_markdown(pull_request)
    return WebExtractorResult(
        name="github-pull-request",
        body=body,
        score=925,
        metadata={
            "repository": f"{pull_request.owner}/{pull_request.repo}",
            "number": pull_request.number,
            "title": pull_request.title,
        },
    )


def is_github_pull_request(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    parts = [part for part in parsed.path.split("/") if part]
    return host == "github.com" and len(parts) >= 4 and parts[2] == "pull" and parts[3].isdigit()


def extract_pull_request(soup: BeautifulSoup, base_url: str) -> GitHubPullRequest | None:
    parsed = urlparse(base_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4:
        return None
    owner, repo, number = parts[0], parts[1], parts[3]
    meta_title = meta_content(soup, "og:title") or meta_content(soup, "twitter:title") or page_title(soup)
    title, author = parse_github_pr_title(meta_title, owner, repo, number)
    if not title:
        title = normalize_text(meta_title or f"Pull Request #{number}")

    comments = extract_pr_comments(soup, base_url)
    description = comments[0]["markdown"] if comments else None
    return GitHubPullRequest(
        owner=owner,
        repo=repo,
        number=number,
        title=title,
        author=author,
        url=base_url,
        description=description,
        comments=comments[1:],
        commits=extract_commit_summaries(soup),
    )


def parse_github_pr_title(value: str | None, owner: str, repo: str, number: str) -> tuple[str | None, str | None]:
    text = normalize_text(value or "")
    pattern = rf"^(?P<title>.+?) by (?P<author>[^ ]+) · Pull Request #{re.escape(number)} · {re.escape(owner)}/{re.escape(repo)}"
    match = re.search(pattern, text)
    if match:
        return normalize_text(match.group("title")), normalize_text(match.group("author"))
    text = re.sub(rf"\s*·\s*Pull Request #{re.escape(number)}\s*·\s*{re.escape(owner)}/{re.escape(repo)}.*$", "", text)
    return (text or None), None


def extract_pr_comments(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    comments = []
    for index, body in enumerate(soup.select(".js-comment-body, .comment-body.markdown-body"), start=1):
        body_copy = deepcopy(body)
        clean_github_comment(body_copy)
        normalize_links(body_copy, base_url)
        markdown = clean_markdown(markdown_from_node(body_copy), "")
        if not markdown:
            continue
        container = body.find_parent(class_=re.compile(r"(timeline-comment|TimelineItem)"))
        author = github_comment_author(container) if container else None
        created_at = github_comment_date(container) if container else None
        comments.append(
            {
                "index": str(index),
                "author": author or "",
                "created_at": created_at or "",
                "markdown": markdown,
            }
        )
    return dedupe_comments(comments)


def clean_github_comment(node) -> None:
    for tag in list(node.select("script, style, task-lists, clipboard-copy, relative-time, .zeroclipboard-container")):
        if tag.parent is not None:
            tag.decompose()
    for tag in node.find_all(True):
        tag.attrs = {
            key: value
            for key, value in tag.attrs.items()
            if key in {"href", "src", "alt", "title"}
        }


def github_comment_author(container) -> str | None:
    for selector in [".author", ".timeline-comment-header a.Link--primary", "a.Link--primary"]:
        node = container.select_one(selector)
        if node:
            text = normalize_text(node.get_text(" ", strip=True))
            if text and text not in {"Contributor", "Member", "Owner"}:
                return text
    return None


def github_comment_date(container) -> str | None:
    node = container.select_one("relative-time, time-ago, time")
    if not node:
        return None
    value = node.get("datetime") or node.get("title") or node.get_text(" ", strip=True)
    return normalize_text(value) if isinstance(value, str) else None


def dedupe_comments(comments: list[dict[str, str]]) -> list[dict[str, str]]:
    result = []
    seen = set()
    for comment in comments:
        key = (comment["author"], comment["markdown"])
        if key in seen:
            continue
        seen.add(key)
        result.append(comment)
    return result


def extract_commit_summaries(soup: BeautifulSoup) -> list[str]:
    commits = []
    for item in soup.select(".TimelineItem, .commit, .commit-group-item"):
        text = normalize_text(item.get_text(" ", strip=True))
        match = re.search(r"([0-9a-f]{7,40})", text)
        if not match:
            continue
        summary = text[:240]
        if summary and summary not in commits:
            commits.append(summary)
        if len(commits) >= 20:
            break
    return commits


def render_pull_request_markdown(pr: GitHubPullRequest) -> str:
    info = [
        f"- 仓库：{pr.owner}/{pr.repo}",
        f"- PR：#{pr.number}",
        f"- 标题：{pr.title}",
        f"- 作者：{pr.author}" if pr.author else "",
        f"- 原始链接：{pr.url}",
    ]
    sections = ["## Pull Request 信息\n\n" + "\n".join(item for item in info if item)]
    if pr.description:
        sections.append("## PR 描述\n\n" + pr.description)
    if pr.comments:
        comment_sections = []
        for comment in pr.comments:
            heading = f"### 评论 {comment['index']}"
            metadata = []
            if comment["author"]:
                metadata.append(f"- 作者：{comment['author']}")
            if comment["created_at"]:
                metadata.append(f"- 时间：{comment['created_at']}")
            prefix = heading
            if metadata:
                prefix += "\n\n" + "\n".join(metadata)
            comment_sections.append(prefix + "\n\n" + comment["markdown"])
        sections.append("## 评论\n\n" + "\n\n".join(comment_sections))
    if pr.commits:
        sections.append("## 页面可见提交\n\n" + "\n".join(f"- {commit}" for commit in pr.commits))
    return "\n\n".join(sections).strip()


def meta_content(soup: BeautifulSoup, key: str) -> str | None:
    tag = soup.find("meta", attrs={"name": key}) or soup.find("meta", attrs={"property": key})
    if not tag:
        return None
    content = tag.get("content")
    return normalize_text(content) if isinstance(content, str) else None


def page_title(soup: BeautifulSoup) -> str | None:
    return normalize_text(soup.title.get_text(" ", strip=True)) if soup.title else None
