import type { Job, SourceType, User } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000/api";

export function getToken() {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("access_token");
}

export function setToken(token: string) {
  window.localStorage.setItem("access_token", token);
}

export function clearToken() {
  window.localStorage.removeItem("access_token");
}

export function getGuestToken(jobId: string) {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(`guest_token:${jobId}`);
}

export function setGuestToken(jobId: string, token: string) {
  window.localStorage.setItem(`guest_token:${jobId}`, token);
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch {
      // Keep status text.
    }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function createJob(formData: FormData): Promise<{ job: Job; guest_token?: string | null }> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_BASE}/jobs`, { method: "POST", body: formData, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "创建任务失败");
  }
  return response.json();
}

export async function getJob(id: string): Promise<Job> {
  const guest = getGuestToken(id);
  const suffix = guest ? `?guest_token=${encodeURIComponent(guest)}` : "";
  return apiFetch<Job>(`/jobs/${id}${suffix}`);
}

export async function getMarkdown(id: string): Promise<string> {
  const guest = getGuestToken(id);
  const suffix = guest ? `?guest_token=${encodeURIComponent(guest)}` : "";
  const payload = await apiFetch<{ markdown: string }>(`/jobs/${id}/markdown${suffix}`);
  return payload.markdown;
}

export function downloadUrl(id: string, format: "md" | "zip") {
  const guest = getGuestToken(id);
  const params = new URLSearchParams({ format });
  if (guest) params.set("guest_token", guest);
  return `${API_BASE}/jobs/${id}/download?${params.toString()}`;
}

export async function deleteJob(id: string) {
  const guest = getGuestToken(id);
  const suffix = guest ? `?guest_token=${encodeURIComponent(guest)}` : "";
  await apiFetch(`/jobs/${id}${suffix}`, { method: "DELETE" });
}

export async function retryJob(id: string): Promise<Job> {
  const guest = getGuestToken(id);
  const suffix = guest ? `?guest_token=${encodeURIComponent(guest)}` : "";
  return apiFetch<Job>(`/jobs/${id}/retry${suffix}`, { method: "POST" });
}

export async function login(email: string, password: string) {
  const payload = await apiFetch<{ access_token: string; user: User }>("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password })
  });
  setToken(payload.access_token);
  return payload.user;
}

export async function register(email: string, password: string) {
  const payload = await apiFetch<{ access_token: string; user: User }>("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password })
  });
  setToken(payload.access_token);
  return payload.user;
}

export async function listJobs(filters: { type?: SourceType | ""; status?: string; search?: string } = {}) {
  const params = new URLSearchParams();
  if (filters.type) params.set("type", filters.type);
  if (filters.status) params.set("status_filter", filters.status);
  if (filters.search) params.set("search", filters.search);
  const query = params.toString();
  return apiFetch<{ jobs: Job[] }>(`/jobs${query ? `?${query}` : ""}`);
}
