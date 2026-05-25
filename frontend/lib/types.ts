export type JobStatus = "pending" | "processing" | "succeeded" | "failed" | "expired";

export type SourceType =
  | "webpage"
  | "text"
  | "html"
  | "csv"
  | "pdf"
  | "docx"
  | "audio"
  | "video"
  | "video_url";

export type Job = {
  id: string;
  source_type: SourceType;
  source_url?: string | null;
  input_filename?: string | null;
  status: JobStatus;
  progress: number;
  title?: string | null;
  language?: string | null;
  duration?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  expires_at: string;
  completed_at?: string | null;
  metadata_json: Record<string, unknown>;
};

export type User = {
  id: string;
  email: string;
  role: "user" | "admin";
  is_active: boolean;
  created_at: string;
};

