"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Copy, Download, RefreshCcw, Trash2 } from "lucide-react";
import { assetUrl, deleteJob, downloadUrl, getJob, getMarkdown, retryJob } from "@/lib/api";
import type { Job } from "@/lib/types";
import { ProgressBar } from "@/components/progress-bar";
import { StatusBadge } from "@/components/status-badge";

export default function JobPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [markdown, setMarkdown] = useState("");
  const [error, setError] = useState("");
  const [viewMode, setViewMode] = useState<"preview" | "source">("preview");
  const [zoomImage, setZoomImage] = useState<{ src: string; alt: string } | null>(null);

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
                <div style={{ display: "inline-flex", width: "fit-content", border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden" }}>
                  <button className="btn" onClick={() => setViewMode("preview")} type="button" style={{ border: 0, borderRadius: 0, background: viewMode === "preview" ? "#e8f1ff" : "#fff" }}>
                    预览
                  </button>
                  <button className="btn" onClick={() => setViewMode("source")} type="button" style={{ border: 0, borderRadius: 0, background: viewMode === "source" ? "#e8f1ff" : "#fff" }}>
                    源码
                  </button>
                </div>
                {viewMode === "preview" ? (
                  <MarkdownPreview markdown={markdown} jobId={job.id} onImageClick={setZoomImage} />
                ) : (
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
                )}
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
            <QualityPanel metadata={job.metadata_json} />
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
      {zoomImage && (
        <button
          type="button"
          onClick={() => setZoomImage(null)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 50,
            border: 0,
            background: "rgba(15, 23, 42, 0.82)",
            padding: 28,
            cursor: "zoom-out"
          }}
        >
          <img src={zoomImage.src} alt={zoomImage.alt} style={{ maxWidth: "94vw", maxHeight: "90vh", objectFit: "contain" }} />
        </button>
      )}
    </main>
  );
}

function QualityPanel({ metadata }: { metadata: Record<string, unknown> }) {
  const score = typeof metadata.quality_score === "number" ? metadata.quality_score : null;
  const status = typeof metadata.quality_status === "string" ? metadata.quality_status : null;
  const reasons = Array.isArray(metadata.quality_reasons) ? metadata.quality_reasons.slice(0, 4) : [];
  const warnings = Array.isArray(metadata.quality_warnings) ? metadata.quality_warnings : [];
  if (score === null || !status) return null;
  return (
    <div style={{ marginTop: 18, borderTop: "1px solid var(--border)", paddingTop: 16 }}>
      <h3 style={{ margin: 0, fontSize: 16 }}>转换质量</h3>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10 }}>
        <strong style={{ fontSize: 28 }}>{score}</strong>
        <span className="muted">{qualityLabel(status)}</span>
      </div>
      {reasons.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
          {reasons.map((reason) => (
            <span key={String(reason)} style={{ border: "1px solid var(--border)", borderRadius: 999, padding: "3px 8px", fontSize: 12 }}>
              {String(reason)}
            </span>
          ))}
        </div>
      )}
      {warnings.length > 0 && <p style={{ margin: "10px 0 0", color: "var(--danger)", fontSize: 13 }}>{warnings.map(String).join(", ")}</p>}
    </div>
  );
}

function qualityLabel(status: string) {
  const labels: Record<string, string> = {
    strong: "强",
    usable: "可用",
    weak: "偏弱",
    failed: "失败"
  };
  return labels[status] || status;
}

function MarkdownPreview({ markdown, jobId, onImageClick }: { markdown: string; jobId: string; onImageClick: (image: { src: string; alt: string }) => void }) {
  const elements = renderMarkdownBlocks(markdown, jobId, onImageClick);
  return (
    <div
      style={{
        minHeight: 520,
        maxHeight: 780,
        overflow: "auto",
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: 22,
        lineHeight: 1.7,
        background: "#fff"
      }}
    >
      {elements}
    </div>
  );
}

function renderMarkdownBlocks(markdown: string, jobId: string, onImageClick: (image: { src: string; alt: string }) => void) {
  const lines = markdown.split(/\r?\n/);
  const elements: ReactNode[] = [];
  let paragraph: string[] = [];
  let code: string[] | null = null;
  let formula: string[] | null = null;

  function flushParagraph() {
    if (!paragraph.length) return;
    elements.push(
      <p key={`p-${elements.length}`} style={{ margin: "0 0 14px" }}>
        {paragraph.join(" ")}
      </p>
    );
    paragraph = [];
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.startsWith("```")) {
      if (code) {
        elements.push(
          <pre key={`code-${elements.length}`} style={{ overflow: "auto", background: "#101828", color: "#f9fafb", borderRadius: 8, padding: 14 }}>
            <code>{code.join("\n")}</code>
          </pre>
        );
        code = null;
      } else {
        flushParagraph();
        code = [];
      }
      continue;
    }
    if (code) {
      code.push(line);
      continue;
    }
    if (line.trim() === "$$") {
      if (formula) {
        elements.push(
          <div key={`formula-${elements.length}`} style={{ overflow: "auto", background: "#f8fafc", border: "1px solid var(--border)", borderRadius: 8, padding: 14, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", whiteSpace: "pre" }}>
            {formula.join("\n")}
          </div>
        );
        formula = null;
      } else {
        flushParagraph();
        formula = [];
      }
      continue;
    }
    if (formula) {
      formula.push(line);
      continue;
    }

    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      continue;
    }

    const image = trimmed.match(/^!\[([^\]]*)]\(([^)]+)\)$/);
    if (image) {
      flushParagraph();
      const src = resolveMarkdownImageSrc(jobId, image[2]);
      elements.push(
        <figure key={`img-${elements.length}`} style={{ margin: "18px 0" }}>
          <button type="button" onClick={() => onImageClick({ src, alt: image[1] })} style={{ border: 0, padding: 0, background: "transparent", cursor: "zoom-in", maxWidth: "100%" }}>
            <img src={src} alt={image[1]} style={{ maxWidth: "100%", borderRadius: 8, border: "1px solid var(--border)" }} />
          </button>
          {image[1] && <figcaption className="muted" style={{ marginTop: 6, fontSize: 13 }}>{image[1]}</figcaption>}
        </figure>
      );
      continue;
    }

    if (isMarkdownTableStart(lines, index)) {
      flushParagraph();
      const tableLines: string[] = [];
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        tableLines.push(lines[index].trim());
        index += 1;
      }
      index -= 1;
      elements.push(renderMarkdownTable(tableLines, elements.length));
      continue;
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      const level = heading[1].length;
      const style = { margin: "20px 0 10px" };
      if (level <= 1) elements.push(<h2 key={`h-${elements.length}`} style={style}>{heading[2]}</h2>);
      else if (level === 2) elements.push(<h3 key={`h-${elements.length}`} style={style}>{heading[2]}</h3>);
      else if (level === 3) elements.push(<h4 key={`h-${elements.length}`} style={style}>{heading[2]}</h4>);
      else elements.push(<h5 key={`h-${elements.length}`} style={style}>{heading[2]}</h5>);
      continue;
    }

    if (trimmed.startsWith(">")) {
      flushParagraph();
      elements.push(
        <blockquote key={`q-${elements.length}`} style={{ margin: "12px 0", paddingLeft: 14, borderLeft: "3px solid var(--border)", color: "var(--muted)" }}>
          {trimmed.replace(/^>\s?/, "")}
        </blockquote>
      );
      continue;
    }

    paragraph.push(trimmed);
  }
  if (formula) {
    elements.push(
      <div key={`formula-${elements.length}`} style={{ overflow: "auto", background: "#f8fafc", border: "1px solid var(--border)", borderRadius: 8, padding: 14, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", whiteSpace: "pre" }}>
        {formula.join("\n")}
      </div>
    );
  }
  flushParagraph();
  return elements;
}

function resolveMarkdownImageSrc(jobId: string, src: string) {
  if (/^https?:\/\//i.test(src) || src.startsWith("data:")) return src;
  if (/^\.?\/?assets\//.test(src)) return assetUrl(jobId, src);
  return src;
}

function isMarkdownTableStart(lines: string[], index: number) {
  return Boolean(
    lines[index]?.trim().startsWith("|") &&
      lines[index + 1]?.trim().match(/^\|(?:\s*:?-{3,}:?\s*\|)+\s*$/)
  );
}

function renderMarkdownTable(lines: string[], key: number) {
  const rows = lines.map((line) => line.trim().slice(1, -1).split("|").map((cell) => cell.trim()));
  const header = rows[0] || [];
  const body = rows.slice(2);
  return (
    <div key={`table-${key}`} style={{ overflow: "auto", margin: "16px 0" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
        <thead>
          <tr>
            {header.map((cell, index) => (
              <th key={index} style={{ textAlign: "left", border: "1px solid var(--border)", padding: "8px 10px", background: "#f8fafc" }}>{cell}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {header.map((_, columnIndex) => (
                <td key={columnIndex} style={{ border: "1px solid var(--border)", padding: "8px 10px", verticalAlign: "top" }}>{row[columnIndex] || ""}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
