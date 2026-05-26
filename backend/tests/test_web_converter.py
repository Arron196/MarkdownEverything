import re

from bs4 import BeautifulSoup

import app.converters.web_extractors.registry as extractor_registry
from app.converters.web import select_content_candidate
from app.converters.web_extractors.base import WebExtractorContext, WebExtractorResult
from app.converters.web_extractors.discourse import discourse_topic_markdown
from app.converters.web_extractors.registry import run_specialized_extractors
from app.converters.web_extractors.snapshot import build_page_snapshot, render_page_snapshot_markdown
from app.converters.web_extractors.utils import (
    clean_markdown,
    extract_image_source,
    markdown_from_node,
    normalize_links,
)


def test_docs_page_extraction_prefers_main_content_over_nav_noise():
    html = """
    <!doctype html>
    <html>
      <head>
        <title>安装和第一个任务 - Trellis Docs</title>
        <meta property="og:title" content="安装和第一个任务" />
      </head>
      <body>
        <nav>
          <a>首页</a><a>快速开始</a><a>API</a><a>参考</a>
          <a>安装</a><a>配置</a><a>部署</a><a>更新日志</a>
        </nav>
        <aside class="sidebar">
          <a>安装和第一个任务</a>
          <a>CLI</a>
          <a>凭证</a>
        </aside>
        <main id="content">
          <section data-agent-docs-index>
            <h1>Documentation Index</h1>
            <p>Use this file to discover all available pages before exploring further.</p>
            <p>llms.txt</p>
          </section>
          <a aria-label="导航到标题" href="#快速开始">#</a>
          <h1>安装和第一个任务</h1>
          <h2 id="快速开始">快速开始</h2>
          <p>本指南会带你安装 Trellis CLI，并运行第一个自动化任务。</p>
          <h3>安装</h3>
          <pre><code>npm install -g @trytrellis/cli</code></pre>
          <h3>创建任务</h3>
          <p>安装完成后，使用下面的命令登录并创建第一个任务。</p>
          <table>
            <tr><th>步骤</th><th>命令</th></tr>
            <tr><td>登录</td><td>trellis login</td></tr>
          </table>
        </main>
        <div class="footer">Subscribe to newsletter</div>
      </body>
    </html>
    """

    candidate = select_content_candidate(html, BeautifulSoup(html, "html.parser"), "https://docs.trytrellis.app/zh/start/install-and-first-task")
    markdown = clean_markdown(markdown_from_node(candidate.node), "安装和第一个任务")

    text = candidate.node.get_text(" ", strip=True)
    assert candidate.name == "#content"
    assert "Documentation Index" not in text
    assert "llms.txt" not in text
    assert "Subscribe to newsletter" not in text
    assert "快速开始" in text
    assert "npm install -g @trytrellis/cli" in text
    assert "trellis login" in text
    assert "导航到标题" not in text
    assert "## 快速开始" in markdown
    assert "```" in markdown
    assert "| 步骤 | 命令 |" in markdown
    assert "安装和第一个任务" not in re.sub(r"\s+", "", markdown)


def test_candidate_scoring_penalizes_link_heavy_sidebar():
    html = """
    <html>
      <body>
        <div class="content">
          <h1>完整文章</h1>
          <p>这是一段很长的正文内容，用来模拟真实文章中的主要段落。它包含足够多的上下文和信息。</p>
          <p>第二段继续说明背景、步骤、限制条件，以及用户真正想要保留下来的细节。</p>
          <pre><code>print("hello markdown")</code></pre>
        </div>
        <div id="sidebar">
          <a href="/a">首页</a><a href="/b">目录</a><a href="/c">搜索</a><a href="/d">导航</a>
          <a href="/e">上一篇</a><a href="/f">下一篇</a><a href="/g">订阅</a><a href="/h">分享</a>
        </div>
      </body>
    </html>
    """

    candidate = select_content_candidate(html, BeautifulSoup(html, "html.parser"), "https://example.com/article")

    assert candidate.name == ".content"
    assert "完整文章" in candidate.node.get_text(" ", strip=True)
    assert "上一篇" not in candidate.node.get_text(" ", strip=True)


def test_clean_markdown_removes_hidden_agent_index_and_duplicate_title():
    markdown = """
    # Example Title

    # Documentation Index

    Use this file to discover all available pages before exploring further.

    llms.txt

    ## 正文

    内容。
    """

    cleaned = clean_markdown(markdown, "Example Title")

    assert "# Example Title" not in cleaned
    assert "Documentation Index" not in cleaned
    assert "## 正文" in cleaned
    assert "内容。" in cleaned


def test_normalize_links_and_image_source_handle_relative_and_lazy_assets():
    html = """
    <main>
      <a href="/docs/start">开始</a>
      <a href="mailto:hi@example.com">邮箱</a>
      <a href="javascript:alert(1)">坏链接</a>
      <img data-src="/images/hero.png" alt="Hero">
      <img srcset="/small.png 320w, /large.png 1200w">
    </main>
    """
    soup = BeautifulSoup(html, "html.parser")
    normalize_links(soup, "https://example.com/base/page")

    links = soup.find_all("a")
    assert links[0]["href"] == "https://example.com/docs/start"
    assert links[1]["href"] == "mailto:hi@example.com"
    assert len(links) == 2
    assert "坏链接" in soup.get_text(" ", strip=True)

    images = soup.find_all("img")
    assert extract_image_source(images[0], "https://example.com/base/page") == "https://example.com/images/hero.png"
    assert extract_image_source(images[1], "https://example.com/base/page") == "https://example.com/large.png"


def test_page_snapshot_outputs_controls_visible_text_and_compact_links():
    html = """
    <html>
      <head>
        <title>搜索 - Example</title>
        <meta name="description" content="用于搜索网页内容的首页。">
      </head>
      <body>
        <form role="search">
          <input type="search" aria-label="输入搜索词" name="q">
          <input type="hidden" name="form" value="HP">
          <input type="submit" value="搜索">
        </form>
        <a href="/images">图片</a>
        <a href="/search?q=%E7%83%AD%E7%82%B9&filters=very-long-noise">1 热点新闻</a>
        <h1>每日壁纸</h1>
        <p>盛开的羽扇豆，北加利福尼亚州，美国</p>
        <img src="/th?id=hero" alt="羽扇豆">
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    snapshot = build_page_snapshot(soup, "https://example.com/", "搜索")
    markdown = render_page_snapshot_markdown(snapshot)

    assert "## 页面描述" in markdown
    assert "input:search - 输入搜索词" in markdown
    assert "input:submit - 搜索" in markdown
    assert "input:hidden" not in markdown
    assert "盛开的羽扇豆" in markdown
    assert "[图片](https://example.com/images)" in markdown
    assert "[1 热点新闻](https://example.com/search?q=%E7%83%AD%E7%82%B9)" in markdown
    assert "[羽扇豆](https://example.com/th?id=hero)" in markdown


def test_discourse_topic_markdown_extracts_posts_without_shell_noise():
    html = """
    <html>
      <head><title>Example Topic - Forum</title></head>
      <body>
        <nav>话题 近期活动 搜索 登录</nav>
        <div id="topic-title"><h1>Example Topic</h1></div>
        <article class="boxed onscreen-post" data-post-id="1" id="post_1">
          <div class="topic-meta-data">
            <span class="username"><a href="/u/alice">alice</a></span>
            <a class="post-date" href="/t/example/1"><span class="relative-date" title="2026 年 5月 1 日 10:00">1 天</span></a>
          </div>
          <div class="cooked">
            <p>第一楼正文，包含 <a href="/docs">文档链接</a>。</p>
            <pre><div class="codeblock-button-wrapper"><button>copy</button></div><code>trellis init -u alice</code></pre>
          </div>
        </article>
        <article class="boxed onscreen-post" data-post-id="2" id="post_2">
          <div class="topic-meta-data">
            <span class="username"><a href="/u/bob">bob</a></span>
            <a class="post-date" href="/t/example/2"><span class="relative-date" title="2026 年 5月 1 日 10:05">1 天</span></a>
          </div>
          <div class="cooked"><p>第二楼回复。</p></div>
        </article>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    markdown = discourse_topic_markdown(soup, "https://forum.example/t/example", "Example Topic")

    assert markdown is not None
    assert "## 帖子目录" in markdown
    assert "## #1 alice" in markdown
    assert "发布时间：2026 年 5月 1 日 10:00" in markdown
    assert "[文档链接](https://forum.example/docs)" in markdown
    assert "trellis init -u alice" in markdown
    assert "codeblock-button-wrapper" not in markdown
    assert "话题 近期活动" not in markdown
    assert "## #2 bob" in markdown


def test_specialized_extractor_registry_selects_highest_scoring_match(monkeypatch):
    soup = BeautifulSoup("<main><p>hello</p></main>", "html.parser")
    context = WebExtractorContext(soup=soup, base_url="https://example.com", title="Example")

    def low_score_extractor(_context):
        return WebExtractorResult(name="low", body="low", score=10)

    def high_score_extractor(_context):
        return WebExtractorResult(name="high", body="high", score=20)

    monkeypatch.setattr(extractor_registry, "SPECIALIZED_EXTRACTORS", [low_score_extractor, high_score_extractor])

    result = run_specialized_extractors(context)

    assert result is not None
    assert result.name == "high"
    assert result.body == "high"
