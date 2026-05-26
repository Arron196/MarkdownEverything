import asyncio
import re

import httpx
import pytest
from bs4 import BeautifulSoup

import app.converters.web_extractors.registry as extractor_registry
import app.converters.web as web_converter
from app.converters.base import ConversionResult
from app.converters.web import select_content_candidate, should_use_specialized_result
from app.converters.web_extractors.base import WebExtractorContext, WebExtractorResult
from app.converters.web_extractors.bilibili import (
    extract_home_videos,
    extract_video_detail,
    render_bilibili_home_markdown,
    render_bilibili_video_markdown,
)
from app.converters.web_extractors.discourse import discourse_topic_markdown
from app.converters.web_extractors.nodeseek import nodeseek_post_markdown
from app.converters.web_extractors.registry import run_specialized_extractors
from app.converters.web_extractors.snapshot import build_page_snapshot, render_page_snapshot_markdown
from app.converters.web_extractors.utils import (
    clean_markdown,
    extract_image_source,
    markdown_from_node,
    normalize_links,
)
from app.converters.web_extractors.wikipedia import extract as wikipedia_extract
from app.converters.web_extractors.wikipedia import wikipedia_article_markdown


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


def test_page_snapshot_outputs_metadata_lists_tables_and_media():
    html = """
    <html>
      <head>
        <title>Example Product</title>
        <meta property="og:site_name" content="Example">
        <script type="application/ld+json">
          {"@type":"Product","name":"Markdown Engine","author":{"name":"ME Team"}}
        </script>
      </head>
      <body>
        <main>
          <h1>Markdown Engine</h1>
          <ul><li>抓取网页</li><li>清洗内容</li><li>生成 Markdown</li></ul>
          <table>
            <tr><th>能力</th><th>状态</th></tr>
            <tr><td>表格</td><td>保留</td></tr>
          </table>
          <video src="/demo.mp4" title="产品演示"></video>
        </main>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    snapshot = build_page_snapshot(soup, "https://example.com/product", "Example Product")
    markdown = render_page_snapshot_markdown(snapshot)

    assert "## 元数据" in markdown
    assert "- og:site_name: Example" in markdown
    assert "- jsonld:type: Product" in markdown
    assert "- jsonld:name: Markdown Engine" in markdown
    assert "## 列表" in markdown
    assert "- 抓取网页" in markdown
    assert "## 表格" in markdown
    assert "| 能力 | 状态 |" in markdown
    assert "| 表格 | 保留 |" in markdown
    assert "## 媒体" in markdown
    assert "[产品演示](https://example.com/demo.mp4)" in markdown


def test_convert_webpage_renders_after_static_request_error(monkeypatch, tmp_path):
    async def fake_fetch_html(_url):
        request = httpx.Request("GET", "https://example.com/app")
        raise httpx.ConnectError("network reset", request=request)

    async def fake_render_page(_url):
        return web_converter.RenderedPage(
            html="""
            <html>
              <head><title>Rendered App</title></head>
              <body><main><h1>Rendered App</h1><p>浏览器渲染后出现的主要内容，足够长，适合 MarkdownEverything 继续转换。</p></main></body>
            </html>
            """,
            title="Rendered App",
            final_url="https://example.com/app",
        )

    async def fake_download_images(*_args, **_kwargs):
        return []

    monkeypatch.setattr(web_converter, "async_playwright", object())
    monkeypatch.setattr(web_converter, "fetch_html", fake_fetch_html)
    monkeypatch.setattr(web_converter, "render_page", fake_render_page)
    monkeypatch.setattr(web_converter, "download_images", fake_download_images)

    result = asyncio.run(web_converter.convert_webpage("https://example.com/app", tmp_path / "assets"))

    assert isinstance(result, ConversionResult)
    assert result.title == "Rendered App"
    assert result.source_url == "https://example.com/app"
    assert "浏览器渲染后出现的主要内容" in result.body


def test_convert_webpage_rejects_access_restriction_response(monkeypatch, tmp_path):
    async def fake_fetch_html(_url):
        return (
            """
            <html>
              <head><title>www.zhihu.com</title></head>
              <body>{"error":{"message":"您当前请求存在异常，暂时限制本次访问。如有疑问，您可以通过手机摇一摇或登录后私信知乎小管家反馈。","code":40362}}</body>
            </html>
            """,
            "https://www.zhihu.com/question/2042235448982860479",
        )

    async def fake_download_images(*_args, **_kwargs):
        return []

    monkeypatch.setattr(web_converter, "fetch_html", fake_fetch_html)
    monkeypatch.setattr(web_converter, "download_images", fake_download_images)

    with pytest.raises(ValueError, match="access restriction"):
        asyncio.run(web_converter.convert_webpage("https://www.zhihu.com/question/2042235448982860479", tmp_path / "assets"))


def test_bilibili_home_extractor_renders_video_table():
    html = """
    <html>
      <body>
        <nav>首页 番剧 直播 游戏中心 会员购</nav>
        <div class="feed-card">
          <div class="bili-video-card is-rcmd">
            <a class="bili-video-card__image--link" href="https://www.bilibili.com/video/BV1aaa"></a>
            <span class="bili-video-card__stats--item"><span class="bili-video-card__stats--text">32.6万</span></span>
            <span class="bili-video-card__stats--item"><span class="bili-video-card__stats--text">886</span></span>
            <span class="bili-video-card__stats__duration">07:26</span>
            <h3 class="bili-video-card__info--tit" title="第一条视频"><a href="https://www.bilibili.com/video/BV1aaa">第一条视频</a></h3>
            <span class="bili-video-card__info--author" title="作者A">作者A</span>
            <span class="bili-video-card__info--date">· 昨天</span>
          </div>
        </div>
        <div class="feed-card">
          <div class="bili-video-card is-rcmd">
            <a class="bili-video-card__image--link" href="//www.bilibili.com/video/BV1bbb"></a>
            <span class="bili-video-card__stats--item"><span class="bili-video-card__stats--text">9.2万</span></span>
            <span class="bili-video-card__stats--item"><span class="bili-video-card__stats--text">215</span></span>
            <span class="bili-video-card__stats__duration">03:11</span>
            <h3 class="bili-video-card__info--tit"><a href="//www.bilibili.com/video/BV1bbb">第二条 | 视频</a></h3>
            <span class="bili-video-card__info--author">作者B</span>
            <span class="bili-video-card__info--date">· 05-24</span>
          </div>
        </div>
        <div class="feed-card">
          <div class="bili-video-card is-rcmd">
            <a class="bili-video-card__image--link" href="https://www.bilibili.com/video/BV1ccc"></a>
            <span class="bili-video-card__stats--item"><span class="bili-video-card__stats--text">1.2万</span></span>
            <span class="bili-video-card__stats--item"><span class="bili-video-card__stats--text">66</span></span>
            <span class="bili-video-card__stats__duration">01:00</span>
            <h3 class="bili-video-card__info--tit"><a href="https://www.bilibili.com/video/BV1ccc">第三条视频</a></h3>
            <span class="bili-video-card__info--author">作者C</span>
            <span class="bili-video-card__info--date">· 05-23</span>
          </div>
        </div>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    videos = extract_home_videos(soup, "https://www.bilibili.com/")
    markdown = render_bilibili_home_markdown(videos)

    assert len(videos) == 3
    assert "首页 番剧" not in markdown
    assert "## 首页推荐视频" in markdown
    assert "| 标题 | 作者 | 播放 | 互动 | 时长 | 日期 | 链接 |" in markdown
    assert "| 第一条视频 | 作者A | 32.6万 | 886 | 07:26 | 昨天 | https://www.bilibili.com/video/BV1aaa |" in markdown
    assert "第二条 \\| 视频" in markdown
    assert "https://www.bilibili.com/video/BV1bbb" in markdown


def test_bilibili_video_extractor_renders_detail_markdown():
    html = r"""
    <html>
      <head>
        <title>第一条视频_哔哩哔哩_bilibili</title>
        <meta name="author" content="作者A">
        <meta name="keywords" content="第一条视频,NBA,马刺,哔哩哔哩,bilibili">
      </head>
      <body>
        <h1 class="video-title">第一条视频</h1>
        <div class="recommend-list">推荐视频 A 推荐视频 B 推荐视频 C</div>
        <script>
          window.__INITIAL_STATE__={
            "videoData":{
              "bvid":"BV1aaa",
              "aid":123,
              "title":"第一条视频",
              "pubdate":1779679313,
              "duration":446,
              "desc":"这是视频简介",
              "pic":"http://i2.hdslb.com/bfs/archive/cover.jpg",
              "tname_v2":"篮球",
              "owner":{"mid":10086,"name":"作者A"},
              "stat":{"view":325806,"danmaku":886,"reply":2273,"like":5874,"coin":715,"favorite":561,"share":345}
            },
            "tags":[{"tag_name":"NBA"},{"tag_name":"马刺"}]
          };(function(){})();
        </script>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    detail = extract_video_detail(soup, "https://www.bilibili.com/video/BV1aaa/", "第一条视频_哔哩哔哩_bilibili")
    markdown = render_bilibili_video_markdown(detail)

    assert detail is not None
    assert detail.bvid == "BV1aaa"
    assert detail.author == "作者A"
    assert detail.duration == "07:26"
    assert detail.cover_url == "https://i2.hdslb.com/bfs/archive/cover.jpg"
    assert "## 视频信息" in markdown
    assert "- UP 主：[作者A](https://space.bilibili.com/10086)" in markdown
    assert "- BV 号：BV1aaa" in markdown
    assert "- 分区：篮球" in markdown
    assert "## 简介\n\n这是视频简介" in markdown
    assert "| 播放 | 325806 |" in markdown
    assert "- NBA" in markdown
    assert "推荐视频" not in markdown


def test_high_confidence_specialized_extractor_can_replace_longer_generic_body():
    result = WebExtractorResult(name="bilibili-video", body="## 视频信息\n\n- 标题：第一条视频", score=920)

    assert should_use_specialized_result(result, "推荐视频\n" * 100, rendered=False)


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


def test_nodeseek_post_markdown_extracts_thread_posts_without_sidebar_noise():
    html = """
    <html>
      <head><title>甲骨文终于申请成功了</title></head>
      <body>
        <aside>
          <h4>所有版块</h4>
          <a href="/categories/daily">日常</a>
          <a href="/lucky">幸运抽奖</a>
        </aside>
        <div class="nsk-post-wrapper">
          <div class="nsk-post">
            <div class="post-title"><a class="post-title-link" href="/post-748049-1">甲骨文终于申请成功了</a></div>
            <div class="content-item" id="0">
              <div class="nsk-content-meta-info">
                <a class="author-name" href="/space/32635">dozeee</a>
                <span class="is-poster role-tag">楼主</span>
                <div class="content-info">1h 52min ago <span class="content-category">in <a href="/categories/daily">日常</a></span></div>
                <a class="floor-link" href="#0">#0</a>
              </div>
              <article class="post-content"><p>刚才刷到别人开机经验贴，我试了下也成功了。<img src="/static/image/sticker/xhj/006.png" alt="xhj006"></p></article>
              <div class="comment-menu">0 0 0 1</div>
            </div>
            <ul class="comments">
              <li class="content-item" id="1">
                <div class="nsk-content-meta-info">
                  <a class="author-name" href="/space/38902">lotfree</a>
                  <div class="content-info">1h 51min ago</div>
                  <a class="floor-link" href="#1">#1</a>
                </div>
                <article class="post-content"><p>恭喜，是用的哪家宽带？</p></article>
                <div class="comment-menu">0 0 0</div>
              </li>
              <li class="content-item" id="4">
                <div class="nsk-content-meta-info">
                  <a class="author-name" href="/space/32635">dozeee</a>
                  <span class="is-poster role-tag">楼主</span>
                  <div class="content-info">1h 49min ago</div>
                  <a class="floor-link" href="#4">#4</a>
                </div>
                <article class="post-content">
                  <p><a href="/member?t=lotfree">@lotfree</a> <a href="/post-748049-1#1">#1</a> 深圳家里联通宽带</p>
                </article>
                <div class="comment-menu">0 1 0</div>
              </li>
            </ul>
          </div>
        </div>
        <section>你好啊，陌生人! 登录 注册 快捷功能区 用户数目</section>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    markdown = nodeseek_post_markdown(soup, "https://www.nodeseek.com/post-748049-1", "甲骨文终于申请成功了")

    assert markdown is not None
    assert "## 主题信息" in markdown
    assert "- 标题：甲骨文终于申请成功了" in markdown
    assert "- 楼主：dozeee" in markdown
    assert "- 版块：日常" in markdown
    assert "## 楼层目录" in markdown
    assert "- #0 dozeee" in markdown
    assert "- #1 lotfree" in markdown
    assert "- #4 dozeee" in markdown
    assert "## #0 dozeee" in markdown
    assert "刚才刷到别人开机经验贴" in markdown
    assert "![xhj006](https://www.nodeseek.com/static/image/sticker/xhj/006.png)" in markdown
    assert "## #4 dozeee" in markdown
    assert "[@lotfree](https://www.nodeseek.com/member?t=lotfree)" in markdown
    assert "所有版块" not in markdown
    assert "快捷功能区" not in markdown
    assert "comment-menu" not in markdown


def test_wikipedia_article_markdown_extracts_article_without_language_or_edit_noise():
    html = """
    <html>
      <head><title>纽约时报 - 维基百科，自由的百科全书</title></head>
      <body>
        <div id="p-lang-btn" class="mw-portlet-lang">
          <a>Afrikaans</a><a>English</a><a>日本語</a>
        </div>
        <h1 id="firstHeading"><span class="mw-page-title-main">纽约时报</span></h1>
        <div id="mw-content-text" class="mw-body-content">
          <div class="mw-parser-output">
            <table class="ambox"><tr><td>此条目可参照英语维基百科扩充。</td></tr></table>
            <div class="hatnote">此条目介绍的是报纸。</div>
            <table class="infobox">
              <tr><th colspan="2">纽约时报</th></tr>
              <tr><td colspan="2"><img src="//upload.wikimedia.org/logo.png" alt="Logo"></td></tr>
              <tr><th>类型</th><td>日报</td></tr>
              <tr><th>创刊日</th><td>1851年9月18日</td></tr>
            </table>
            <p>《纽约时报》（英语：<i>The New York Times</i>）是一份总部设在
              <a href="/wiki/%E7%BA%BD%E7%BA%A6">纽约</a> 的美国报纸。<sup class="reference">[1]</sup>
            </p>
            <div class="mw-heading mw-heading2"><h2>历史 <span class="mw-editsection">[编辑]</span></h2></div>
            <p>1851年9月18日，亨利·J·雷蒙德和乔治·琼斯创立了《纽约每日时报》。</p>
            <div class="navbox">展开 查 论 编 美国报纸</div>
            <div class="reflist"><ol><li>参考资料噪声</li></ol></div>
          </div>
        </div>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    markdown = wikipedia_article_markdown(
        soup,
        "https://zh.wikipedia.org/zh-cn/%E7%BA%BD%E7%BA%A6%E6%97%B6%E6%8A%A5",
        "纽约时报",
    )

    assert markdown is not None
    assert markdown.startswith("# 纽约时报")
    assert "## 摘要" in markdown
    assert "## 正文" in markdown
    assert "| 类型 | 日报 |" in markdown
    assert "[Logo](https://upload.wikimedia.org/logo.png)" in markdown
    assert "## 图片资源" in markdown
    assert "[纽约](https://zh.wikipedia.org/wiki/%E7%BA%BD%E7%BA%A6)" in markdown
    assert "## 历史" in markdown
    assert "1851年9月18日" in markdown
    assert "Afrikaans" not in markdown
    assert "[编辑]" not in markdown
    assert "此条目可参照" not in markdown
    assert "展开 查 论 编" not in markdown
    assert "参考资料噪声" not in markdown


def test_wikipedia_extractor_supports_all_language_domains_and_variant_paths():
    html = """
    <html>
      <body>
        <h1 id="firstHeading"><span class="mw-page-title-main">Example Article</span></h1>
        <div id="mw-content-text">
          <div class="mw-parser-output">
            <p>This article has enough text to be treated as a real Wikipedia article across language editions.
            It should be extracted from MediaWiki parser output rather than matched by a language-specific URL.</p>
            <div class="mw-heading mw-heading2"><h2>History</h2></div>
            <p>History paragraph with useful encyclopedic content and internal links.</p>
          </div>
        </div>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")

    urls = [
        "https://en.wikipedia.org/wiki/The_New_York_Times",
        "https://ja.wikipedia.org/wiki/%E3%83%8B%E3%83%A5%E3%83%BC%E3%83%A8%E3%83%BC%E3%82%AF",
        "https://zh.wikipedia.org/zh-cn/%E7%BA%BD%E7%BA%A6%E6%97%B6%E6%8A%A5",
        "https://fr.wikipedia.org/wiki/Le_Monde",
    ]

    for url in urls:
        result = wikipedia_extract(WebExtractorContext(soup=soup, base_url=url, title="Example Article"))
        assert result is not None
        assert result.name == "wikipedia-article"
        assert "Example Article" in result.body
        assert "History paragraph" in result.body


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
