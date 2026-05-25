export function ProgressBar({ value }: { value: number }) {
  const safeValue = Math.max(0, Math.min(100, value));
  return (
    <div style={{ width: "100%", height: 10, borderRadius: 999, background: "#e6ebf1", overflow: "hidden" }}>
      <div
        style={{
          width: `${safeValue}%`,
          height: "100%",
          borderRadius: 999,
          background: "var(--primary)",
          transition: "width 240ms ease"
        }}
      />
    </div>
  );
}

