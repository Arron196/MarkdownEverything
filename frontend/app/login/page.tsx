"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { login, register } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, password);
      }
      router.push("/history");
    } catch (err) {
      setError(err instanceof Error ? err.message : "认证失败");
    }
  }

  return (
    <main className="container" style={{ padding: "46px 0" }}>
      <form className="panel" onSubmit={submit} style={{ width: "min(460px, 100%)", margin: "0 auto", padding: 22, display: "grid", gap: 14 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 26 }}>{mode === "login" ? "登录" : "注册"}</h1>
          <p className="muted" style={{ margin: "8px 0 0" }}>登录后可以查看历史记录和保留 7 天结果。</p>
        </div>
        <input className="input" type="email" placeholder="邮箱" value={email} onChange={(event) => setEmail(event.target.value)} required />
        <input className="input" type="password" placeholder="密码，至少 8 位" value={password} onChange={(event) => setPassword(event.target.value)} required />
        {error && <div style={{ color: "var(--danger)", fontSize: 14 }}>{error}</div>}
        <button className="btn btn-primary" type="submit">{mode === "login" ? "登录" : "注册"}</button>
        <button className="btn" type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "创建新账号" : "已有账号，去登录"}
        </button>
      </form>
    </main>
  );
}

