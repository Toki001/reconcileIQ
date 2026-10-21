from celery import Celery
from reconcileiq_database.config import get_settings

celery_app = Celery("reconcileiq", broker=get_settings().broker_url.get_secret_value())
celery_app.conf.update(
    imports=("reconcileiq_worker.tasks",),
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # RabbitMQ 4.3 rejects transient, non-exclusive queues. Control/event
    # queues are connection-local; business task queues remain durable.
    control_queue_exclusive=True,
    event_queue_exclusive=True,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    task_publish_retry_policy={
        "max_retries": 2,
        "interval_start": 0,
        "interval_step": 1,
        "interval_max": 2,
    },
    task_soft_time_limit=20,
    task_time_limit=30,
)
