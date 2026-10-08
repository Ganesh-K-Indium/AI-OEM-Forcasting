from celery import Celery

from app.core.config import get_settings

s = get_settings()
celery_app = Celery("oem", broker=s.redis_url, backend=s.redis_url)
celery_app.conf.update(task_track_started=True, task_serializer="json", result_serializer="json", worker_prefetch_multiplier=1, task_acks_late=True)


@celery_app.task(name="oem.run_job")
def run_job(job_id: str) -> str:
    from app.tasks.jobs import execute_job

    execute_job(job_id)
    return job_id
