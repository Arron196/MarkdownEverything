"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { Download, Search, Trash2 } from "lucide-react";
import { deleteJob, downloadUrl, listJobs } from "@/lib/api";
import type { Job, SourceType } from "@/lib/types";
import { StatusBadge } from "@/components/status-badge";

export default function HistoryPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [type, setType] = useState<SourceType | "">("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");

  async function load() {
    try {
      const payload = await listJobs({ type, status, search });
      setJobs(payload.jobs);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取历史记录失败");
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    await load();
  }

  async function remove(id: string) {
    await deleteJob(id);
    await load();
  }

  return (
    <main className="container" style={{ padding: "30px 0 54px" }}>
      <h1 style={{ margin: 0, fontSize: 28 }}>历史记录</h1>
      <form className="panel" onSubmit={submit} style={{ display: "grid", gridTemplateColumns: "1fr 180px 180px auto", gap: 10, padding: 14, marginTop: 18 }}>
        <input className="input" placeholder="搜索标题、文件名或 URL" value={search} onChange={(event) => setSearch(event.target.value)} />
        <select className="input" value={type} onChange={(event) => setType(event.target.value as SourceType | "")}>
          <option value="">全部类型</option>
          <option value="webpage">网页</option>
          <option value="pdf">PDF</option>
          <option value="docx">DOCX</option>
          <option value="audio">音频</option>
          <option value="video">视频</option>
          <option value="video_url">视频链接</option>
        </select>
        <select className="input" value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">全部状态</option>
          <option value="pending">等待中</option>
          <option value="processing">转换中</option>
          <option value="succeeded">转换成功</option>
          <option value="failed">转换失败</option>
          <option value="expired">已过期</option>
        </select>
        <button className="btn btn-primary" type="submit">
          <Search size={17} /> 搜索
        </button>
      </form>
      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
      <section className="panel" style={{ marginTop: 16, overflow: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 820 }}>
          <thead style={{ background: "#f2f5f8", textAlign: "left" }}>
            <tr>
              <th style={cellStyle}>任务</th>
              <th style={cellStyle}>类型</th>
              <th style={cellStyle}>状态</th>
              <th style={cellStyle}>创建时间</th>
              <th style={cellStyle}>操作</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id} style={{ borderTop: "1px solid var(--line)" }}>
                <td style={cellStyle}>
                  <Link href={`/jobs/${job.id}`} style={{ fontWeight: 700 }}>
                    {job.title || job.input_filename || job.source_url || job.id}
                  </Link>
                  <div className="muted" style={{ fontSize: 13, maxWidth: 420, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {job.source_url || job.input_filename || job.id}
                  </div>
                </td>
                <td style={cellStyle}>{job.source_type}</td>
                <td style={cellStyle}><StatusBadge status={job.status} /></td>
                <td style={cellStyle}>{new Date(job.created_at).toLocaleString()}</td>
                <td style={{ ...cellStyle, display: "flex", gap: 8 }}>
                  {job.status === "succeeded" && (
                    <a className="btn" href={downloadUrl(job.id, "zip")} title="重新下载结果">
                      <Download size={16} />
                    </a>
                  )}
                  <button className="btn btn-danger" type="button" onClick={() => remove(job.id)} title="删除任务">
                    <Trash2 size={16} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}

const cellStyle: React.CSSProperties = {
  padding: "12px 14px",
  verticalAlign: "middle",
  fontSize: 14
};

