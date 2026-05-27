# MarkdownEverything

<p align="center">
  English | <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <img src="docs/readme-hero.png" alt="MarkdownEverything converts webpages, documents, audio, and video into clean Markdown" width="100%">
</p>

<p align="center">
  <strong>Convert webpages, documents, text, audio, and video into clean Markdown you can read, archive, search, and pass to AI tools.</strong>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a>
  · <a href="#what-it-converts">Converters</a>
  · <a href="#webpage-engine">Web Engine</a>
  · <a href="#api">API</a>
  · <a href="#roadmap">Roadmap</a>
</p>

<p align="center">
  <img alt="Next.js" src="https://img.shields.io/badge/Next.js-App%20Router-111827?style=flat-square&logo=nextdotjs">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-Backend-059669?style=flat-square&logo=fastapi&logoColor=white">
  <img alt="Celery" src="https://img.shields.io/badge/Celery-Workers-37814A?style=flat-square">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-Storage-4169E1?style=flat-square&logo=postgresql&logoColor=white">
  <img alt="Docker Compose" src="https://img.shields.io/badge/Docker%20Compose-Ready-2496ED?style=flat-square&logo=docker&logoColor=white">
</p>

MarkdownEverything is an open-source service for turning messy source material into usable Markdown. It fetches content, extracts the useful parts, keeps source metadata, saves related assets, and packages the result for reading, search, RAG, agent workflows, Obsidian-style archives, and future MCP integrations.

## Why It Exists

Useful material often lives in awkward formats: webpages full of navigation, PDFs with brittle text extraction, DOCX files with embedded assets, long recordings, video pages, forum threads, and app-like public pages. MarkdownEverything normalizes those inputs into a small set of files:

- frontmatter with source metadata
- clean Markdown body
- downloaded assets
- timestamped audio/video transcripts
- result `.md` and `.zip`
- task metadata for debugging and automation

## What It Converts

| Input | Status | Notes |
| --- | --- | --- |
| Webpage URL | Working | SSRF-safe fetch, generic extraction, browser render fallback, snapshots for app/home/search pages |
| Plain text | Working | Unified Markdown output |
| HTML | Working | HTML to Markdown cleanup |
| CSV | Working | Markdown table output |
| PDF | Working | Text-first PDF extraction |
| DOCX | Working | Headings, paragraphs, tables, and image export |
| Audio files | Working | ASR provider abstraction, timestamped Markdown |
| Video files | Working | ffmpeg audio extraction, ASR timeline |
| Public video links | Working | yt-dlp for accessible public media only; no DRM/login/paywall bypass |

## Interface

- Home page for URLs, file uploads, text, HTML, and supported input types
- Task page with status, progress, errors, retry, and result links
- Result page with Markdown preview, source details, copy, `.md` download, `.zip` download, delete, and retry
- User history with type and status filters, search, delete, and redownload
- Admin dashboard for users, jobs, failed logs, workers, storage, and health checks

## Architecture

```mermaid
flowchart LR
  U["User / API Client"] --> N["nginx"]
  N --> F["Next.js frontend"]
  N --> B["FastAPI backend"]
  B --> P["PostgreSQL"]
  B --> R["Redis"]
  B --> S["Local storage"]
  R --> WW["worker-web"]
  R --> WF["worker-file"]
  R --> WA["worker-audio"]
  R --> WV["worker-video"]
  WW --> S
  WF --> S
  WA --> S
  WV --> S
```

The Docker Compose setup runs:

- `frontend`: Next.js App Router + TypeScript
- `backend`: FastAPI + SQLAlchemy
- `worker-web`, `worker-file`, `worker-audio`, `worker-video`: Celery workers split by queue
- `postgres`: job/user/log metadata
- `redis`: Celery broker/result backend
- `nginx`: single entrypoint

## Quick Start

```powershell
cp .env.example .env
docker compose up --build
```

Open:

- App: <http://localhost>
- API docs: <http://localhost/api/docs>

The first registered user becomes an admin unless you set `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD`.

For local development without Redis workers:

```powershell
cd backend
$env:SYNC_CONVERSIONS="true"
python -m uvicorn app.main:app --reload
```

## Configuration

Text, HTML, CSV, PDF, DOCX, and most webpage conversions work without API keys.

Audio and video transcription needs one ASR provider:

| Mode | Environment |
| --- | --- |
| Local Whisper | `ASR_PROVIDER=local_whisper`, optional `LOCAL_WHISPER_MODEL=base` |
| OpenAI-compatible ASR | `ASR_PROVIDER=cloud_openai_compatible`, `ASR_API_KEY`, optional `ASR_BASE_URL`, `ASR_MODEL` |

AI summaries use an OpenAI-compatible chat endpoint:

```env
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=
AI_MODEL=gpt-4.1-mini
```

If `AI_API_KEY` is not set, conversion still succeeds with an extractive fallback summary.

## Webpage Engine

The web converter uses deterministic candidate selection instead of a "longest text wins" scraper.

It analyzes the page, builds candidate outputs, scores them, and keeps the best result that passes safety and quality checks.

Candidate sources include:

- specialized extractors: Bilibili, GitHub PR, NodeSeek, Wikipedia, Discourse
- semantic containers: `article`, `main`, `[role=main]`, docs/prose containers
- Readability and Trafilatura extraction
- JSON-LD article data
- heuristic DOM subtrees
- rendered snapshots for home/search/list/SPAs

Each conversion records metadata such as:

```json
{
  "extractor": "trafilatura",
  "extractor_score": 378.32,
  "quality_status": "strong",
  "candidate_count": 5,
  "rendered": false,
  "winner_source": "trafilatura"
}
```

See [docs/web-extractors.md](docs/web-extractors.md) for the extractor contract and contribution guide.

## Benchmarking

The repository includes a compatibility corpus with more than 100 sites:

```powershell
cd backend
$env:PYTHONIOENCODING="utf-8"
python benchmarks\run_web_compat.py --limit 10 --timeout 35 --concurrency 2
```

Full run:

```powershell
python benchmarks\run_web_compat.py --timeout 45 --concurrency 4 --retry-failed --retry-timeout 60 --retry-concurrency 1
```

Reports are written to `backend/benchmarks/results/` and ignored by git.

## API

Base path: `/api`

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/jobs` | Create a conversion task |
| `GET` | `/jobs` | List authenticated user's jobs |
| `GET` | `/jobs/{id}` | Get task status and metadata |
| `GET` | `/jobs/{id}/markdown` | Read Markdown result |
| `GET` | `/jobs/{id}/download?format=md` | Download Markdown |
| `GET` | `/jobs/{id}/download?format=zip` | Download result archive |
| `POST` | `/jobs/{id}/retry` | Retry a finished task |
| `DELETE` | `/jobs/{id}` | Soft-delete a task and clean files |

Guest tasks return a `guest_token`. Authenticated users get their own history. Admins can inspect system state from the admin dashboard.

## Storage Layout

MVP storage uses local disk:

```text
/data/markdown-everything/
  jobs/
    {job_id}/
      input/
      output/
      assets/
      logs/
```

Retention defaults:

- guest jobs: 24 hours
- logged-in user jobs: 7 days
- raw input files can be cleaned earlier than results

## Security Boundary

MarkdownEverything handles public and user-provided content. It does not bypass access controls.

Implemented safeguards include:

- upload extension and MIME validation
- upload size limits
- SSRF protection for webpage and image fetches
- private/reserved network blocking
- redirect, timeout, response-size, and render limits
- per-user and guest concurrency limits
- authenticated downloads
- user task isolation
- task-level logs and visible failure messages
- scheduled cleanup

## Development

Backend tests:

```powershell
cd backend
python -m pytest -q
```

Frontend dev:

```powershell
cd frontend
npm install
npm run dev
```

Useful paths:

- `backend/app/converters/web.py`: webpage orchestration
- `backend/app/converters/web_engine/`: deterministic generic web engine
- `backend/app/converters/web_extractors/`: specialized site/page extractors
- `backend/app/tasks.py`: Celery conversion task flow
- `frontend/app/`: Next.js pages
- `docs/web-extractors.md`: extractor contribution guide

## Roadmap

Planned after the MVP:

- batch conversion
- OCR and scanned PDF support
- automatic tags and title refinement
- Obsidian export
- Git repository export
- API keys
- webhooks
- MCP server
- RAG chunk export
- Markdown templates
- multi-file merge and summary

## License

No license has been declared yet. Add one before using this as a public open-source project.
