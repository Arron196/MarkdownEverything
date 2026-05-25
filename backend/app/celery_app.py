from celery import Celery

from app.config import settings


celery_app = Celery(
    "markdown_everything",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    timezone="UTC",
    task_routes={
        "app.tasks.process_job": {"queue": "file"},
        "app.tasks.cleanup_expired_jobs": {"queue": "file"},
    },
    beat_schedule={
        "cleanup-expired-jobs-hourly": {
            "task": "app.tasks.cleanup_expired_jobs",
            "schedule": 3600.0,
        }
    },
)

if settings.celery_always_eager:
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
