import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "MarkdownEverything",
  description: "Convert content into clean, structured Markdown."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="app-shell">
          <header style={{ borderBottom: "1px solid var(--line)", background: "#fff" }}>
            <div className="container" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", minHeight: 64 }}>
              <Link href="/" style={{ fontWeight: 800, fontSize: 20 }}>
                MarkdownEverything
              </Link>
              <nav style={{ display: "flex", gap: 14, alignItems: "center", color: "var(--muted)", fontSize: 14 }}>
                <Link href="/history">历史记录</Link>
                <Link href="/admin">管理后台</Link>
                <Link href="/login">登录</Link>
              </nav>
            </div>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}

