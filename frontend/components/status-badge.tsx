import type { JobStatus } from "@/lib/types";

const labels: Record<JobStatus, string> = {
  pending: "等待中",
  processing: "转换中",
  succeeded: "转换成功",
  failed: "转换失败",
  expired: "已过期"
};

const colors: Record<JobStatus, string> = {
  pending: "#7a4f01",
  processing: "#175cd3",
  succeeded: "#027a48",
  failed: "#b42318",
  expired: "#667085"
};

export function statusLabel(status: JobStatus) {
  return labels[status];
}

export function StatusBadge({ status }: { status: JobStatus }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        minHeight: 26,
        padding: "0 9px",
        borderRadius: 999,
        background: `${colors[status]}17`,
        color: colors[status],
        fontSize: 13,
        fontWeight: 700
      }}
    >
      {labels[status]}
    </span>
  );
}

