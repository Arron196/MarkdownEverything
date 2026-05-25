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

