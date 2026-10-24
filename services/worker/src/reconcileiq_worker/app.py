from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown
from reconcileiq_database.config import get_settings
from reconcileiq_telemetry.integrations import worker_child, worker_shutdown

from reconcileiq_worker import logging as safe_logging  # noqa: F401
from reconcileiq_worker.dispatch import INVOICE_QUEUE, PROBE_QUEUE, RecoveryService

worker_process_init.connect(worker_child, weak=False)
worker_process_shutdown.connect(worker_shutdown, weak=False)

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
    worker_concurrency=get_settings().worker_concurrency,
    worker_max_tasks_per_child=50,
    worker_max_memory_per_child=256000,
    task_queues=(INVOICE_QUEUE, PROBE_QUEUE),
    task_default_queue=PROBE_QUEUE.name,
    task_default_exchange="reconcileiq.tasks",
    task_default_routing_key="probe",
    task_routes={
        "reconcileiq.process_invoice": {"queue": INVOICE_QUEUE.name, "routing_key": "invoice"}
    },
    task_default_delivery_mode="persistent",
    broker_transport_options={"confirm_publish": True},
    broker_heartbeat=30,
    task_acks_on_failure_or_timeout=False,
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
    task_soft_time_limit=40,
    task_time_limit=60,
)
celery_app.steps["worker"].add(RecoveryService)
