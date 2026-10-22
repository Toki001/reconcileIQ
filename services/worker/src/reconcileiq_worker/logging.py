"""Worker diagnostics deliberately omit raw exception chains and task/provider contents."""

import logging
import re
from urllib.parse import urlsplit

from celery.signals import after_setup_logger, after_setup_task_logger
from reconcileiq_database.config import get_settings
from sqlalchemy.engine import make_url


class SafeDiagnostics(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        settings = get_settings()
        message = record.getMessage()
        values = [
            settings.database_url,
            settings.broker_url,
            settings.minio_secret_key,
            settings.admin_token,
            settings.reviewer_token,
            settings.auditor_token,
            settings.cursor_secret,
        ]
        hidden = [value.get_secret_value() for value in values]
        hidden.extend(
            [
                make_url(settings.database_url.get_secret_value()).password or "",
                urlsplit(settings.broker_url.get_secret_value()).password or "",
            ]
        )
        hidden.extend(
            supplier.secret.get_secret_value() for supplier in settings.supplier_webhooks.values()
        )
        for value in sorted(set(hidden), key=len, reverse=True):
            if value:
                message = message.replace(value, "[redacted]")
        message = re.sub(
            r"(?:postgresql(?:\+psycopg)?|amqps?)://\S+", "[redacted connection]", message
        )
        record.msg, record.args = message, ()
        record.exc_info = record.exc_text = None
        return True


@after_setup_logger.connect  # type: ignore[untyped-decorator]
@after_setup_task_logger.connect  # type: ignore[untyped-decorator]
def install_safe_logging(**kwargs: object) -> None:
    for logger in (logging.getLogger(), logging.getLogger("celery.task")):
        for handler in logger.handlers:
            handler.addFilter(SafeDiagnostics())
    # Malformed PDFs can contain sensitive strings in parser warning messages.
    logging.getLogger("pypdf").disabled = True
    logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)
