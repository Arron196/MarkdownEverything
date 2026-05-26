import argparse
import asyncio
import json
import re
import tempfile
import time
import sys
from collections.abc import Callable
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from urllib.parse import urlparse

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.converters.web import convert_webpage


def load_sites(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    sites = data.get("sites", [])
    if not isinstance(sites, list):
        raise ValueError("Benchmark file must contain a sites list")
    return sites


def score_markdown(markdown: str, extractor: str | None) -> dict:
    text = re.sub(r"\s+", " ", markdown).strip()
    headings = len(re.findall(r"(?m)^#{1,6}\s+", markdown))
    links = len(re.findall(r"\[[^\]]+\]\([^)]+\)", markdown))
    lists = len(re.findall(r"(?m)^[-*]\s+", markdown))
    code_blocks = markdown.count("```") // 2
    tables = len(re.findall(r"(?m)^\|.+\|$", markdown))
    sections = sum(
        marker in markdown
        for marker in ["## 页面描述", "## 可见文本", "## 主要链接", "## 正文", "## 帖子目录", "## 页面标题结构"]
    )

    score = 0
    score += min(len(text), 3000) / 30
    score += min(headings, 12) * 4
    score += min(links, 20) * 1.5
    score += min(lists, 30)
    score += min(code_blocks, 5) * 6
    score += min(tables, 10) * 3
    score += sections * 10
    if extractor and extractor not in {"body-fallback"}:
        score += 15
    if len(text) < 120:
        score -= 50
    if "未检测到可提取的可见正文内容" in markdown:
        score -= 30

    return {
        "score": round(max(score, 0), 2),
        "text_length": len(text),
        "headings": headings,
        "links": links,
        "lists": lists,
        "code_blocks": code_blocks,
        "tables": tables,
    }


def status_from_score(score: float) -> str:
    if score >= 85:
        return "strong"
    if score >= 55:
        return "usable"
    if score >= 25:
        return "weak"
    return "failed"


def classify_failure(markdown: str, title: str) -> str | None:
    text = re.sub(r"\s+", " ", f"{title} {markdown}").lower()
    challenge_markers = [
        "checking your browser",
        "正在检查您的浏览器",
        "client challenge",
        "just a moment",
        "enable javascript",
        "javascript is disabled",
    ]
    access_markers = [
        "登录或注册",
        "sign in",
        "log in",
        "login",
        "your request has been blocked",
        "access denied",
        "forbidden",
    ]
    if any(marker in text for marker in challenge_markers):
        return "challenge_or_js_required"
    if any(marker in text for marker in access_markers):
        return "login_or_blocked"
    return None


def site_identity(site: dict) -> dict:
    url = site["url"]
    return {
        "url": url,
        "domain": urlparse(url).netloc,
        "category": site.get("category", "unknown"),
    }


async def run_one(site: dict, timeout_seconds: int) -> dict:
    url = site["url"]
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_seconds):
            with tempfile.TemporaryDirectory() as tmp:
                result = await convert_webpage(url, Path(tmp) / "assets")
        metrics = score_markdown(result.body, result.metadata.get("extractor"))
        status = status_from_score(metrics["score"])
        failure_reason = classify_failure(result.body, result.title) if status == "failed" else None
        if status == "failed" and not failure_reason and metrics["text_length"] < 120:
            failure_reason = "empty_or_blocked_response"
        return {
            **site_identity(site),
            "attempt": site.get("attempt", 1),
            "retry_of": site.get("retry_of"),
            "ok": status != "failed",
            "status": status,
            "title": result.title,
            "extractor": result.metadata.get("extractor"),
            "extractor_score": result.metadata.get("extractor_score"),
            "quality_status": result.metadata.get("quality_status"),
            "quality_reasons": result.metadata.get("quality_reasons", []),
            "candidate_count": result.metadata.get("candidate_count"),
            "rendered": result.metadata.get("rendered"),
            "winner_source": result.metadata.get("winner_source"),
            "top_candidates": result.metadata.get("top_candidates", []),
            "source_url": result.source_url,
            "failure_reason": failure_reason,
            "duration_seconds": round(time.perf_counter() - started, 2),
            **metrics,
        }
    except Exception as exc:
        return {
            **site_identity(site),
            "attempt": site.get("attempt", 1),
            "retry_of": site.get("retry_of"),
            "ok": False,
            "status": "error",
            "score": 0,
            "error_type": exc.__class__.__name__,
            "error": str(exc)[:500],
            "failure_reason": "network_or_timeout",
            "duration_seconds": round(time.perf_counter() - started, 2),
        }


async def run_benchmark(
    sites: list[dict],
    limit: int | None,
    timeout_seconds: int,
    concurrency: int,
    on_result: Callable[[dict, int, int], None] | None = None,
) -> list[dict]:
    selected = sites[:limit] if limit else sites
    semaphore = asyncio.Semaphore(concurrency)
    total = len(selected)

    async def guarded(site: dict) -> dict:
        async with semaphore:
            return await run_one(site, timeout_seconds)

    tasks = [asyncio.create_task(guarded(site)) for site in selected]
    results: list[dict] = []
    for index, task in enumerate(asyncio.as_completed(tasks), start=1):
        result = await task
        results.append(result)
        if on_result:
            on_result(result, index, total)
    return results


async def retry_failed_results(
    results: list[dict],
    timeout_seconds: int,
    concurrency: int,
    on_result: Callable[[dict, int, int], None] | None = None,
) -> list[dict]:
    failed_sites = [
        {
            "url": item["url"],
            "category": item.get("category", "unknown"),
            "attempt": item.get("attempt", 1) + 1,
            "retry_of": item["url"],
        }
        for item in results
        if not item.get("ok")
    ]
    if not failed_sites:
        return results

    retry_results = await run_benchmark(
        failed_sites,
        limit=None,
        timeout_seconds=timeout_seconds,
        concurrency=concurrency,
        on_result=on_result,
    )
    by_url = {item["url"]: item for item in retry_results if item.get("ok")}
    if not by_url:
        return results

    merged: list[dict] = []
    for item in results:
        replacement = by_url.get(item["url"])
        if replacement:
            replacement["first_attempt_status"] = item.get("status")
            replacement["first_attempt_error_type"] = item.get("error_type")
            merged.append(replacement)
        else:
            merged.append(item)
    return merged


def summarize(results: list[dict]) -> dict:
    total = len(results)
    ok = sum(1 for item in results if item.get("ok"))
    scores = [item.get("score", 0) for item in results]
    by_status = Counter(item.get("status", "unknown") for item in results)
    by_quality = Counter(item.get("quality_status", "unknown") for item in results if item.get("ok"))
    by_extractor = Counter(item.get("extractor", "error") for item in results)
    by_winner_source = Counter(item.get("winner_source", "error") for item in results if item.get("ok"))
    by_failure_reason = Counter(item.get("failure_reason") for item in results if item.get("failure_reason"))
    by_category = defaultdict(lambda: {"total": 0, "ok": 0})
    for item in results:
        bucket = by_category[item.get("category", "unknown")]
        bucket["total"] += 1
        bucket["ok"] += 1 if item.get("ok") else 0
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "ok": ok,
        "success_rate": round(ok / total, 4) if total else 0,
        "average_score": round(mean(scores), 2) if scores else 0,
        "status_counts": dict(by_status),
        "quality_counts": dict(by_quality),
        "extractor_counts": dict(by_extractor),
        "winner_source_counts": dict(by_winner_source),
        "failure_reason_counts": dict(by_failure_reason),
        "rendered_count": sum(1 for item in results if item.get("rendered")),
        "retry_successes": sum(1 for item in results if item.get("ok") and item.get("attempt", 1) > 1),
        "category_success": {
            key: {
                "total": value["total"],
                "ok": value["ok"],
                "success_rate": round(value["ok"] / value["total"], 4) if value["total"] else 0,
            }
            for key, value in sorted(by_category.items())
        },
    }


def write_markdown_report(path: Path, summary: dict, results: list[dict]) -> None:
    lines = [
        "# Web Compatibility Benchmark",
        "",
        f"- Generated: {summary['generated_at']}",
        f"- Sites: {summary['total']}",
        f"- Success rate: {summary['success_rate']:.2%}",
        f"- Average score: {summary['average_score']}",
        f"- Retry successes: {summary.get('retry_successes', 0)}",
        "",
        "## Status Counts",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in summary["status_counts"].items())
    if summary.get("quality_counts"):
        lines.extend(["", "## Quality Counts", ""])
        lines.extend(f"- {key}: {value}" for key, value in summary["quality_counts"].items())
    lines.extend(["", "## Extractors", ""])
    lines.extend(f"- {key}: {value}" for key, value in summary["extractor_counts"].items())
    if summary.get("winner_source_counts"):
        lines.extend(["", "## Winner Sources", ""])
        lines.extend(f"- {key}: {value}" for key, value in summary["winner_source_counts"].items())
    if summary.get("failure_reason_counts"):
        lines.extend(["", "## Failure Reasons", ""])
        lines.extend(f"- {key}: {value}" for key, value in summary["failure_reason_counts"].items())
    lines.extend(["", "## Results", ""])
    lines.append("| Status | Quality | Score | Attempt | Category | Extractor | Source | Candidates | Rendered | URL | Reason | Error |")
    lines.append("| --- | --- | ---: | ---: | --- | --- | --- | ---: | --- | --- | --- | --- |")
    for item in sorted(results, key=lambda value: (value.get("ok", False), value.get("score", 0))):
        lines.append(
            "| {status} | {quality} | {score} | {attempt} | {category} | {extractor} | {source} | {candidates} | {rendered} | {url} | {reason} | {error} |".format(
                status=item.get("status", ""),
                quality=item.get("quality_status", ""),
                score=item.get("score", 0),
                attempt=item.get("attempt", 1),
                category=item.get("category", ""),
                extractor=item.get("extractor", ""),
                source=item.get("winner_source", ""),
                candidates=item.get("candidate_count") or "",
                rendered="yes" if item.get("rendered") else "",
                url=item.get("url", ""),
                reason=item.get("failure_reason") or "",
                error=(item.get("error") or "").replace("|", "\\|"),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_reports(out_path: Path, markdown_path: Path, results: list[dict], total_sites: int | None = None) -> dict:
    summary = summarize(results)
    payload = {"summary": summary, "results": results}
    if total_sites is not None:
        payload["summary"]["target_total"] = total_sites
        payload["summary"]["completed_rate"] = round(len(results) / total_sites, 4) if total_sites else 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_report(markdown_path, payload["summary"], results)
    return payload["summary"]


def progress_line(result: dict, index: int, total: int) -> str:
    marker = "OK" if result.get("ok") else "NO"
    return (
        f"[{index:03d}/{total:03d}] {marker} {result.get('status')} "
        f"{result.get('score', 0):>6} {result.get('quality_status') or '-'} "
        f"{result.get('extractor') or '-'} {result['url']}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run webpage-to-Markdown compatibility benchmark")
    parser.add_argument("--sites", type=Path, default=Path("benchmarks/web_100_sites.yml"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=35)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed URLs once after the first pass")
    parser.add_argument("--retry-timeout", type=int, default=60)
    parser.add_argument("--retry-concurrency", type=int, default=1)
    parser.add_argument("--out", type=Path, default=Path("benchmarks/results/web_compat_latest.json"))
    parser.add_argument("--markdown", type=Path, default=Path("benchmarks/results/web_compat_latest.md"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sites = load_sites(args.sites)
    target_total = len(sites[: args.limit] if args.limit else sites)
    results: list[dict] = []

    def on_result(result: dict, index: int, total: int) -> None:
        results.append(result)
        print(progress_line(result, index, total), flush=True)
        write_reports(args.out, args.markdown, results, total_sites=target_total)

    asyncio.run(run_benchmark(sites, args.limit, args.timeout, args.concurrency, on_result=on_result))
    if args.retry_failed:
        print("Retrying failed URLs...", flush=True)

        def on_retry_result(result: dict, index: int, total: int) -> None:
            print("retry " + progress_line(result, index, total), flush=True)

        results = asyncio.run(
            retry_failed_results(
                results,
                timeout_seconds=args.retry_timeout,
                concurrency=args.retry_concurrency,
                on_result=on_retry_result,
            )
        )
    summary = write_reports(args.out, args.markdown, results, total_sites=target_total)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
