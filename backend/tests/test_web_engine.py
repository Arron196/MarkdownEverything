from bs4 import BeautifulSoup

from app.converters.web_engine.analysis import analyze_page
from app.converters.web_engine.candidates import generate_candidates, snapshot_candidate
from app.converters.web_engine.decision import select_winner, should_render_static_page


def candidates_for(html: str, url: str = "https://example.com/article"):
    soup = BeautifulSoup(html, "html.parser")
    analysis = analyze_page(soup, url)
    candidates = generate_candidates(html, soup, analysis)
    return analysis, candidates


def test_long_navigation_page_cannot_beat_short_structured_article():
    nav_links = "".join(f'<a href="/{index}">导航 链接 {index}</a>' for index in range(80))
    html = f"""
    <html>
      <head><title>完整文章</title></head>
      <body>
        <div id="menu">{nav_links}</div>
        <article>
          <h1>完整文章</h1>
          <p>第一段正文包含足够的信息密度和上下文，用来描述真正应该保留下来的知识内容。</p>
          <p>第二段继续解释背景、条件、限制和实践步骤，让 Markdown 输出可以被人和 AI 可靠阅读。</p>
          <p>第三段补充结构化细节，避免导航链接集合因为文本更长而错误胜出。</p>
        </article>
      </body>
    </html>
    """

    analysis, candidates = candidates_for(html)
    winner = select_winner(candidates, analysis)

    assert winner is not None
    assert winner.name == "article"
    assert winner.metrics.rho <= 0.55
    assert "导航 链接" not in winner.markdown


def test_high_link_density_candidate_is_downgraded():
    links = " ".join(f'<a href="/tag/{index}">关键词链接{index}</a>' for index in range(30))
    html = f"""
    <html><body>
      <main>{links}<p>少量说明文字。</p></main>
    </body></html>
    """

    analysis, candidates = candidates_for(html, "https://example.com/tags")
    main_candidate = next(candidate for candidate in candidates if candidate.name == "main")

    assert main_candidate.metrics.rho > 0.60
    assert main_candidate.quality_status == "weak"
    assert main_candidate.score < 0


def test_repeated_text_candidate_is_penalized_by_unique_ngram_ratio():
    repeated = "".join("<p>这一段重复内容用于检测重复惩罚机制是否生效并降低候选质量。</p>" for _ in range(16))
    html = f"<html><body><article><h1>重复页面</h1>{repeated}</article></body></html>"

    analysis, candidates = candidates_for(html)
    winner = select_winner(candidates, analysis)

    assert winner is not None
    assert winner.metrics.U < 0.75
    assert winner.metrics.duplication_cost > 0


def test_title_similarity_avoids_github_search_box_title():
    html = """
    <html>
      <head>
        <title>Search code, repositories, users, issues, pull requests...</title>
        <meta property="og:title" content="fix(ops): bound memory growth by wucm667 · Pull Request #2752 · Wei-Shaw/sub2api">
      </head>
      <body>
        <main>
          <article>
            <h1>fix(ops): bound memory growth</h1>
            <p>This pull request explains the memory growth fix with enough useful body text to be extracted.</p>
            <p>It includes context, test notes, and operational behavior for reviewers.</p>
            <p>The content should align with the Open Graph title, not with the GitHub search shell title.</p>
          </article>
        </main>
      </body>
    </html>
    """

    analysis, candidates = candidates_for(html, "https://github.com/Wei-Shaw/sub2api/pull/2752")
    winner = select_winner(candidates, analysis)

    assert analysis.title.startswith("fix(ops): bound memory growth")
    assert winner is not None
    assert winner.metrics.TS > 0.35


def test_duplicate_title_candidates_keep_highest_scoring_source():
    html = """
    <html>
      <head>
        <title>安装 && 快速上手使用 - Trellis Doc</title>
        <meta property="og:title" content="安装 && 快速上手使用 - Trellis Doc">
        <script type="application/ld+json">{"@type":"WebSite","name":"Trellis Doc"}</script>
      </head>
      <body><main><h1>安装 && 快速上手使用</h1><p>正文内容足够长，可以用于验证标题候选选择。</p></main></body>
    </html>
    """
    analysis, _candidates = candidates_for(html, "https://docs.trytrellis.app/zh/start/install-and-first-task")

    assert analysis.title == "安装 && 快速上手使用"


def test_wikipedia_edit_marker_is_removed_from_title():
    html = """
    <html>
      <head><title>纽约时报 - 维基百科，自由的百科全书</title></head>
      <body><h1 id="firstHeading">纽约时报 [ 编辑 ]</h1><main><p>正文内容足够长，可以用于验证标题清理。</p></main></body>
    </html>
    """
    analysis, _candidates = candidates_for(html, "https://zh.wikipedia.org/zh-cn/%E7%BA%BD%E7%BA%A6%E6%97%B6%E6%8A%A5")

    assert analysis.title == "纽约时报"


def test_strong_static_candidate_does_not_trigger_render():
    paragraph_texts = [
        "第一段说明系统目标：把公开网页中的正文、标题、代码、表格和图片说明转换成干净 Markdown，供知识库和检索系统复用，并保证输出能直接进入归档流程。",
        "第二段讨论安全约束：抓取前必须校验 URL，拒绝内网地址、过多重定向、异常内容类型和明显的访问挑战页面，同时保留可诊断的失败原因。",
        "第三段描述候选生成：语义节点、正文提取器、结构化数据和启发式子树会同时进入评分池，避免单一规则失效，并让专用抽取器作为高置信度候选参与选择。",
        "第四段解释评分原则：内容长度、段落密度、标题结构、代码块、表格和低链接密度共同决定候选是否可靠，重复文本和导航噪声会受到明确惩罚。",
        "第五段记录清洗策略：导航、目录、广告、分享按钮、订阅框和隐藏内容会被移除，正文链接则保留为绝对地址，图片会继续交给资源下载流程处理。",
        "第六段强调多语言能力：中文、英文、日文和其他语言都通过可见文本、结构特征和字符级重复检测进行统一处理，不依赖某一种语言的停用词表。",
        "第七段给出渲染决策：静态候选足够强时不启动浏览器，只有 SPA、弱正文或可恢复的请求失败才触发 Playwright，从而节省资源并降低不确定性。",
        "第八段用于验证输出：最终结果必须包含可解释元数据，让工程师知道候选数量、胜出来源、质量状态和主要评分理由，便于后续 benchmark 调优并快速定位回归原因，确保维护者能够持续改进。",
    ]
    paragraphs = "".join(f"<p>{text} 这段还包含可验证的实施细节、质量判断依据和回归检测信息。</p>" for text in paragraph_texts)
    html = f"<html><body><article><h1>强静态文章</h1>{paragraphs}</article></body></html>"

    analysis, candidates = candidates_for(html)
    winner = select_winner(candidates, analysis)

    assert winner is not None
    assert winner.quality_status == "strong"
    assert should_render_static_page(winner, analysis, render_available=True) is False


def test_weak_spa_candidate_triggers_render():
    html = """
    <html>
      <head><title>SPA App</title><script src="/assets/chunk.js"></script></head>
      <body><div id="root"></div><script src="/assets/bundle.js"></script></body>
    </html>
    """

    analysis, candidates = candidates_for(html, "https://example.com/app")
    winner = select_winner(candidates, analysis)

    assert analysis.dom_has_spa_signature is True
    assert should_render_static_page(winner, analysis, render_available=True) is True


def test_blocked_page_is_not_treated_as_article():
    html = """
    <html>
      <head><title>Access Denied</title></head>
      <body>Access denied. CAPTCHA verification is required before continuing.</body>
    </html>
    """

    analysis, candidates = candidates_for(html, "https://example.com/protected")
    winner = select_winner(candidates, analysis)

    assert analysis.blocked_reason in {"access_denied", "captcha"}
    assert winner is None or winner.quality_status == "blocked"
    assert should_render_static_page(winner, analysis, render_available=True) is False


def test_snapshot_is_usable_for_search_page_but_not_strong():
    html = """
    <html>
      <head><title>Search</title></head>
      <body>
        <form role="search"><input type="search" aria-label="Search query"></form>
        <a href="/news">News</a><a href="/images">Images</a>
        <h1>Daily links</h1><p>Search the public web and browse current shortcuts.</p>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    analysis = analyze_page(soup, "https://example.com/search?q=markdown")

    candidate = snapshot_candidate(soup, analysis)

    assert candidate is not None
    assert candidate.source == "snapshot"
    assert candidate.quality_status in {"weak", "usable"}
    assert candidate.quality_status != "strong"


def test_homepage_spa_prefers_snapshot_over_weak_text_extractor():
    html = """
    <html>
      <head><title>Video Home</title><meta name="description" content="Watch public videos."></head>
      <body>
        <div id="root">
          <main>
            <h1>Explore</h1>
            <a href="/shorts">Shorts</a><a href="/music">Music</a><a href="/live">Live</a>
            <p>Sign in to like videos, comment, and subscribe.</p>
            <p>Start watching videos to help us recommend content you may enjoy.</p>
          </main>
        </div>
        <script src="/assets/chunk.js"></script>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    analysis = analyze_page(soup, "https://example.com/")
    candidates = generate_candidates(html, soup, analysis)
    candidates.append(snapshot_candidate(soup, analysis))

    winner = select_winner(candidates, analysis)

    assert winner is not None
    assert winner.source == "snapshot"
    assert winner.quality_status == "usable"
