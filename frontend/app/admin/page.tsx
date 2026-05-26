"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import type { Job, User } from "@/lib/types";
import { StatusBadge } from "@/components/status-badge";

type Stats = {
  users: number;
  jobs: number;
  pending: number;
  processing: number;
  failed: number;
  storage_bytes: number;
};

type Capabilities = {
  ffmpeg: boolean;
  ffprobe: boolean;
  yt_dlp: boolean;
  local_asr: boolean;
  cloud_asr_configured: boolean;
  ai_summary_configured: boolean;
  asr_provider: string;
};

type LogRow = {
  id: number;
  job_id: string;
  level: string;
  message: string;
  created_at: string;
};

export default function AdminPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [logs, setLogs] = useState<LogRow[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const [statsPayload, usersPayload, jobsPayload, logsPayload] = await Promise.all([
          apiFetch<Stats>("/admin/stats"),
          apiFetch<User[]>("/admin/users"),
          apiFetch<Job[]>("/admin/jobs"),
          apiFetch<LogRow[]>("/admin/logs")
        ]);
        const health = await apiFetch<{ capabilities: Capabilities }>("/admin/health");
        setStats(statsPayload);
        setUsers(usersPayload);
        setJobs(jobsPayload);
        setLogs(logsPayload);
        setCapabilities(health.capabilities);
      } catch (err) {
        setError(err instanceof Error ? err.message : "读取管理后台失败");
      }
    }
    load();
  }, []);

  return (
    <main className="container" style={{ padding: "30px 0 54px" }}>
      <h1 style={{ margin: 0, fontSize: 28 }}>管理后台</h1>
      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

      {stats && (
        <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12, marginTop: 18 }}>
          <Metric label="用户" value={stats.users} />
          <Metric label="任务" value={stats.jobs} />
          <Metric label="等待中" value={stats.pending} />
          <Metric label="转换中" value={stats.processing} />
          <Metric label="失败" value={stats.failed} />
          <Metric label="存储占用" value={`${(stats.storage_bytes / 1024 / 1024).toFixed(1)} MB`} />
        </section>
      )}

      <div className="grid-dashboard" style={{ marginTop: 18 }}>
        <section className="panel" style={{ padding: 16, overflow: "auto" }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>任务管理</h2>
          <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 10, minWidth: 620 }}>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id} style={{ borderTop: "1px solid var(--line)" }}>
                  <td style={cellStyle}>
                    <Link href={`/jobs/${job.id}`}>{job.title || job.input_filename || job.id}</Link>
                    <div className="muted" style={{ fontSize: 12 }}>{job.source_type}</div>
                  </td>
                  <td style={cellStyle}><StatusBadge status={job.status} /></td>
                  <td style={cellStyle}>{new Date(job.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <aside style={{ display: "grid", gap: 18 }}>
          <section className="panel" style={{ padding: 16 }}>
            <h2 style={{ margin: 0, fontSize: 18 }}>Worker 状态</h2>
            <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
              {capabilities &&
                Object.entries({
                  ffmpeg: capabilities.ffmpeg,
                  ffprobe: capabilities.ffprobe,
                  "yt-dlp": capabilities.yt_dlp,
                  "本地 ASR": capabilities.local_asr,
                  "云端 ASR": capabilities.cloud_asr_configured,
                  "AI 摘要": capabilities.ai_summary_configured
                }).map(([label, ok]) => (
                  <div key={label} style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                    <span>{label}</span>
                    <strong style={{ color: ok ? "var(--primary)" : "var(--danger)" }}>{ok ? "可用" : "未配置"}</strong>
                  </div>
                ))}
              {capabilities && <div className="muted" style={{ fontSize: 13 }}>ASR provider: {capabilities.asr_provider}</div>}
            </div>
          </section>

          <section className="panel" style={{ padding: 16 }}>
            <h2 style={{ margin: 0, fontSize: 18 }}>用户管理</h2>
            <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
              {users.map((user) => (
                <div key={user.id} style={{ display: "flex", justifyContent: "space-between", gap: 10, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
                  <span>{user.email}</span>
                  <strong>{user.role}</strong>
                </div>
              ))}
            </div>
          </section>

          <section className="panel" style={{ padding: 16 }}>
            <h2 style={{ margin: 0, fontSize: 18 }}>转换失败日志</h2>
            <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
              {logs.filter((row) => row.level === "error").slice(0, 8).map((row) => (
                <div key={row.id} style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
                  <strong style={{ color: "var(--danger)" }}>{row.message}</strong>
                  <div className="muted" style={{ fontSize: 12 }}>{row.job_id}</div>
                </div>
              ))}
            </div>
          </section>
        </aside>
      </div>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="panel" style={{ padding: 16 }}>
      <div className="muted" style={{ fontSize: 13 }}>{label}</div>
      <strong style={{ display: "block", fontSize: 24, marginTop: 6 }}>{value}</strong>
    </div>
  );
}

const cellStyle: React.CSSProperties = {
  padding: "11px 8px",
  fontSize: 14,
  verticalAlign: "middle"
};
