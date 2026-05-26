"use client";

import { ChangeEvent, FormEvent, useMemo, useState } from "react";
import { FileAudio, FileText, FileVideo, Globe, UploadCloud } from "lucide-react";
import { useRouter } from "next/navigation";
import { createJob, setGuestToken } from "@/lib/api";

type Mode = "url" | "file" | "text" | "html";

export default function HomePage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("url");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [sourceType, setSourceType] = useState("webpage");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const supported = useMemo(
    () => [
      ["网页 URL", "正文提取、图片归档、元数据保留", Globe],
      ["PDF / DOCX / HTML / TXT / CSV", "标题、段落、表格、附件资源", FileText],
      ["音频", "语音识别、语言识别、时间戳分段", FileAudio],
      ["视频文件 / 视频链接", "提取音频、转写、生成时间轴", FileVideo]
    ],
    []
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      if (mode === "url") {
        if (!url.trim()) throw new Error("请输入 URL");
        form.set("url", url.trim());
        form.set("source_type", sourceType);
      }
      if (mode === "file") {
        if (!file) throw new Error("请选择要转换的文件");
        form.set("file", file);
      }
      if (mode === "text") {
        if (!text.trim()) throw new Error("请输入文本内容");
        form.set("text", text);
      }
      if (mode === "html") {
        if (!text.trim()) throw new Error("请输入 HTML 内容");
        form.set("html", text);
      }
      const response = await createJob(form);
      if (response.guest_token) setGuestToken(response.job.id, response.guest_token);
      router.push(`/jobs/${response.job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建任务失败");
    } finally {
      setBusy(false);
    }
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.[0] || null);
  }

  return (
    <main className="container" style={{ padding: "34px 0 54px" }}>
      <section style={{ display: "grid", gap: 18 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 36, lineHeight: 1.12 }}>粘贴链接、上传文件，立即转换为 Markdown。</h1>
          <p className="muted" style={{ maxWidth: 760, marginTop: 12, fontSize: 17 }}>
            把网页、文档、文本、音频和视频整理成干净、结构化、适合 AI 阅读和知识库归档的 Markdown。
          </p>
        </div>

        <div className="grid-dashboard">
          <form className="panel" onSubmit={submit} style={{ padding: 20, display: "grid", gap: 16 }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {(["url", "file", "text", "html"] as Mode[]).map((item) => (
                <button
                  className={mode === item ? "btn btn-primary" : "btn"}
                  key={item}
                  type="button"
                  onClick={() => setMode(item)}
                >
                  {item === "url" ? "链接" : item === "file" ? "文件" : item === "text" ? "纯文本" : "HTML"}
                </button>
              ))}
            </div>

            {mode === "url" && (
              <div style={{ display: "grid", gap: 10 }}>
                <input className="input" placeholder="https://example.com/article-or-video" value={url} onChange={(event) => setUrl(event.target.value)} />
                <select className="input" value={sourceType} onChange={(event) => setSourceType(event.target.value)}>
                  <option value="webpage">网页 URL</option>
                  <option value="video_url">公开视频链接</option>
                </select>
              </div>
            )}

            {mode === "file" && (
              <label
                style={{
                  display: "grid",
                  placeItems: "center",
                  minHeight: 190,
                  border: "1px dashed var(--line)",
                  borderRadius: 8,
                  background: "#fbfcfe",
                  cursor: "pointer",
                  textAlign: "center",
                  padding: 18
                }}
              >
                <UploadCloud size={32} color="var(--primary)" />
                <strong style={{ marginTop: 8 }}>{file ? file.name : "选择或拖入文件"}</strong>
                <span className="muted" style={{ marginTop: 4 }}>
                  {file ? `${formatBytes(file.size)} · ${file.type || "未知类型"}` : "支持 PDF、DOCX、HTML、TXT、CSV、音频和视频"}
                </span>
                {file && isMediaFile(file.name) && (
                  <span className="muted" style={{ marginTop: 6, fontSize: 13 }}>
                    音视频会提取音频并转写。Docker 部署已内置 ffmpeg，本地运行需要配置 ASR。
                  </span>
                )}
                <input type="file" onChange={onFileChange} style={{ display: "none" }} />
              </label>
            )}

            {(mode === "text" || mode === "html") && (
              <textarea
                className="input"
                style={{ minHeight: 220, resize: "vertical" }}
                placeholder={mode === "text" ? "粘贴需要整理的纯文本" : "粘贴 HTML 源码"}
                value={text}
                onChange={(event) => setText(event.target.value)}
              />
            )}

            {error && <div style={{ color: "var(--danger)", fontSize: 14 }}>{error}</div>}
            <button className="btn btn-primary" type="submit" disabled={busy}>
              {busy ? "正在创建任务" : "开始转换"}
            </button>
          </form>

          <aside className="panel" style={{ padding: 20 }}>
            <h2 style={{ margin: 0, fontSize: 18 }}>支持的内容类型</h2>
            <div style={{ display: "grid", gap: 12, marginTop: 16 }}>
              {supported.map(([title, description, Icon]) => (
                <div key={title as string} style={{ display: "grid", gridTemplateColumns: "36px 1fr", gap: 10, alignItems: "start" }}>
                  <span style={{ width: 36, height: 36, display: "grid", placeItems: "center", borderRadius: 8, background: "#e7f5f3", color: "var(--primary)" }}>
                    <Icon size={19} />
                  </span>
                  <span>
                    <strong>{title as string}</strong>
                    <p className="muted" style={{ margin: "3px 0 0", fontSize: 14 }}>
                      {description as string}
                    </p>
                  </span>
                </div>
              ))}
            </div>
          </aside>
        </div>
      </section>
    </main>
  );
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function isMediaFile(name: string) {
  return /\.(mp3|wav|m4a|aac|ogg|flac|mp4|mov|mkv|webm)$/i.test(name);
}
