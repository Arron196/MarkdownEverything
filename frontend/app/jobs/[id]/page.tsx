"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Copy, Download, RefreshCcw, Trash2 } from "lucide-react";
import { deleteJob, downloadUrl, getJob, getMarkdown, retryJob } from "@/lib/api";
import type { Job } from "@/lib/types";
import { ProgressBar } from "@/components/progress-bar";
import { StatusBadge } from "@/components/status-badge";

export default function JobPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [markdown, setMarkdown] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function load() {
      try {
        const nextJob = await getJob(params.id);
        if (cancelled) return;
        setJob(nextJob);
        if (nextJob.status === "succeeded") {
          const text = await getMarkdown(params.id);
          if (!cancelled) setMarkdown(text);
        }
        if (nextJob.status === "pending" || nextJob.status === "processing") {
          timer = setTimeout(load, 1800);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "读取任务失败");
      }
    }

    load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [params.id]);

  async function copyMarkdown() {
    await navigator.clipboard.writeText(markdown);
  }

  async function remove() {
    await deleteJob(params.id);
    router.push("/");
  }

  async function retry() {
    setMarkdown("");
    setError("");
    const nextJob = await retryJob(params.id);
    setJob(nextJob);
  }

  return (
    <main className="container" style={{ padding: "30px 0 54px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "center", marginBottom: 18 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 28 }}>{job?.title || "转换任务"}</h1>
          <p className="muted" style={{ margin: "6px 0 0" }}>
            {job?.source_url || job?.input_filename || params.id}
          </p>
        </div>
        {job && <StatusBadge status={job.status} />}
      </div>

      {error && <div className="panel" style={{ padding: 16, color: "var(--danger)" }}>{error}</div>}

      {job && (
        <div className="grid-dashboard">
          <section className="panel" style={{ padding: 18, display: "grid", gap: 16 }}>
            <div style={{ display: "grid", gap: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 14 }}>
                <span>转换进度</span>
                <strong>{job.progress}%</strong>
              </div>
              <ProgressBar value={job.progress} />
            </div>

            {job.error_message && (
              <div style={{ border: "1px solid #f3b8b2", background: "#fff4f2", borderRadius: 8, padding: 12, color: "var(--danger)" }}>
                {job.error_message}
              </div>
            )}

            {job.status === "succeeded" ? (
              <>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  <button className="btn" onClick={copyMarkdown} type="button">
                    <Copy size={17} /> 复制 Markdown
                  </button>
                  <a className="btn" href={downloadUrl(job.id, "md")}>
                    <Download size={17} /> 下载 .md
                  </a>
                  <a className="btn" href={downloadUrl(job.id, "zip")}>
                    <Download size={17} /> 下载 .zip
                  </a>
                  <button className="btn btn-danger" onClick={remove} type="button">
                    <Trash2 size={17} /> 删除任务
                  </button>
                </div>
                <pre
                  style={{
                    margin: 0,
                    minHeight: 520,
                    maxHeight: 780,
                    overflow: "auto",
                    whiteSpace: "pre-wrap",
                    background: "#101828",
                    color: "#f9fafb",
                    borderRadius: 8,
                    padding: 18,
                    lineHeight: 1.65,
                    fontSize: 14
                  }}
                >
                  {markdown}
                </pre>
              </>
            ) : (
              <div style={{ minHeight: 420, display: "grid", placeItems: "center", color: "var(--muted)" }}>
                任务完成后会在这里显示 Markdown 预览。
              </div>
            )}
          </section>

          <aside className="panel" style={{ padding: 18, alignSelf: "start" }}>
            <h2 style={{ margin: 0, fontSize: 18 }}>原始信息</h2>
            <dl style={{ display: "grid", gridTemplateColumns: "96px 1fr", gap: "10px 12px", marginTop: 16, fontSize: 14 }}>
              <dt className="muted">任务 ID</dt>
              <dd style={{ margin: 0, wordBreak: "break-all" }}>{job.id}</dd>
              <dt className="muted">类型</dt>
              <dd style={{ margin: 0 }}>{job.source_type}</dd>
              <dt className="muted">文件/链接</dt>
              <dd style={{ margin: 0, wordBreak: "break-all" }}>{job.input_filename || job.source_url || "-"}</dd>
              <dt className="muted">语言</dt>
              <dd style={{ margin: 0 }}>{job.language || "-"}</dd>
              <dt className="muted">时长</dt>
              <dd style={{ margin: 0 }}>{job.duration || "-"}</dd>
              <dt className="muted">过期时间</dt>
              <dd style={{ margin: 0 }}>{new Date(job.expires_at).toLocaleString()}</dd>
            </dl>
            <div style={{ display: "grid", gap: 8, marginTop: 18 }}>
              {job.status === "failed" && (
                <button className="btn" type="button" onClick={retry}>
                  <RefreshCcw size={17} /> 重新转换
                </button>
              )}
              <Link className="btn" href="/">
                <RefreshCcw size={17} /> 重新转换
              </Link>
            </div>
          </aside>
        </div>
      )}
    </main>
  );
}
