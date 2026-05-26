# MarkdownEverything

MarkdownEverything converts webpages, documents, text, audio, and video into clean, structured Markdown for people, AI summaries, knowledge bases, RAG, and agent workflows.

## MVP Stack

- Frontend: Next.js App Router + TypeScript
- Backend: FastAPI + SQLAlchemy + PostgreSQL
- Queue: Celery + Redis
- Workers: web, file, audio, video
- Storage: local disk under `/data/markdown-everything`
- Deployment: Docker Compose with nginx

## Quick Start

```powershell
cp .env.example .env
docker compose up --build
```

Open:

- App: http://localhost
- API docs: http://localhost/api/docs

The first registered user becomes an admin unless `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` are provided.

## What Works In This MVP

- Webpage URL to Markdown with SSRF protection, browser-render fallback, modular extractors, readable-content extraction, metadata, and image asset download.
- Text, HTML, CSV, PDF, and DOCX conversion with unified Markdown frontmatter and ZIP download.
- Audio and video conversion through ffmpeg plus ASR. Docker installs ffmpeg and faster-whisper; the first local Whisper run downloads the selected model from Hugging Face.
- Public video links through yt-dlp, limited to accessible public media and without bypassing login, paywalls, or DRM.
- Email login, guest jobs, job isolation, admin dashboard, failure logs, retry, expiration cleanup, and stuck-job timeout handling.

## Required Configuration For Full Functionality

Text, HTML, CSV, PDF, DOCX, and most webpage conversions work without API keys.

Audio/video transcription needs one of:

- Local ASR: keep `ASR_PROVIDER=local_whisper`. The Docker image includes `faster-whisper`; set `LOCAL_WHISPER_MODEL=base` or another supported model.
- Cloud ASR: set `ASR_PROVIDER=cloud_openai_compatible`, `ASR_API_KEY`, optional `ASR_BASE_URL`, and `ASR_MODEL`.

AI summaries need `AI_API_KEY`. Without it, the app uses an extractive fallback summary so conversion still succeeds.

## Extending Webpage Conversion

Site-specific webpage support is modular. Add extractors under `backend/app/converters/web_extractors/` and register them in `registry.py`.

See `docs/web-extractors.md` for the extractor contract and contribution guidelines.

For local development without Redis workers, set:

```powershell
$env:SYNC_CONVERSIONS="true"
```
