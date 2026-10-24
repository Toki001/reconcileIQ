"""Bounded digital text providers. Document text is evidence, never financial authority."""

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from io import BytesIO
from math import ceil
from typing import Protocol
from urllib.parse import urlsplit

from billiard.exceptions import SoftTimeLimitExceeded
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader, apply_configuration
from reconcileiq_database.config import Settings
from reconcileiq_telemetry import instrument
from urllib3 import PoolManager, Timeout


@dataclass
class ProcessingError(Exception):
    code: str
    retryable: bool = False
    retry_after: int = 0


class TextPage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    page: int = Field(ge=1, le=20)
    text: str = Field(max_length=50_000)


class DocumentText(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: str = "pypdf-digital-text-v1"
    pages: list[TextPage] = Field(min_length=1, max_length=20)


class ObjectReader(Protocol):
    def read(self, organization: str, key: str, size: int, digest: str) -> bytes: ...


class TextProvider(Protocol):
    def extract(self, data: bytes) -> DocumentText: ...


def retry_after_seconds(value: str | None, at: datetime | None = None) -> int:
    if not value:
        return 0
    try:
        if value.strip().isdigit():
            seconds = int(value)
        else:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            seconds = max(0, ceil((deadline - (at or datetime.now(UTC))).total_seconds()))
    except (ValueError, TypeError, OverflowError):
        return 0
    if seconds > 86400:
        # Do not retry earlier than the provider requested, or strand capacity indefinitely.
        raise ProcessingError("RETRY_AFTER_OUTSIDE_WINDOW")
    return seconds


def verify_bytes(data: bytes, size: int, digest: str) -> bytes:
    if len(data) != size or not data.startswith(b"%PDF-"):
        raise ProcessingError("INVALID_PDF")
    if hashlib.sha256(data).hexdigest() != digest:
        raise ProcessingError("CHECKSUM_MISMATCH")
    return data


class MinioReader:
    def __init__(self, settings: Settings) -> None:
        endpoint = urlsplit(str(settings.minio_endpoint))
        self.bucket = settings.minio_bucket
        self.client = Minio(
            endpoint.netloc,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=endpoint.scheme == "https",
            region="us-east-1",
            http_client=PoolManager(timeout=Timeout(connect=3, read=10), retries=False),
        )

    @instrument("storage.read")
    def read(self, organization: str, key: str, size: int, digest: str) -> bytes:
        if not re.fullmatch(re.escape(organization) + r"/sealed/[a-f0-9]{32}\.pdf", key):
            raise ProcessingError("UNSAFE_OBJECT_REFERENCE")
        try:
            metadata = self.client.stat_object(self.bucket, key)
            if metadata.size != size or metadata.content_type != "application/pdf":
                raise ProcessingError("OBJECT_METADATA_MISMATCH")
            response = self.client.get_object(self.bucket, key)
            try:
                data = response.read(size + 1)
            finally:
                response.close()
                response.release_conn()
            return verify_bytes(data, size, digest)
        except ProcessingError:
            raise
        except S3Error as error:
            if error.code in {"NoSuchKey", "NoSuchObject"}:
                raise ProcessingError("OBJECT_MISSING", True) from None
            if error.code in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"}:
                raise ProcessingError("STORAGE_CONFIGURATION", False) from None
            delay = retry_after_seconds(error.response.headers.get("Retry-After"))
            raise ProcessingError("STORAGE_UNAVAILABLE", True, delay) from None
        except Exception:
            raise ProcessingError("STORAGE_UNAVAILABLE", True) from None


class DigitalPdfProvider:
    @instrument("pdf.digital")
    def extract(self, data: bytes) -> DocumentText:
        logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)
        with apply_configuration(
            maximum_declared_stream_length=8_000_000,
            zlib_maximum_output_length=8_000_000,
            stream_decoding_work_maximum_length=16_000_000,
            stream_parsing_work_maximum_count=200_000,
            page_tree_maximum_entries=1000,
            xform_maximum_invocations_per_extraction=100,
            jbig2dec_binary=None,
        ):
            return self._extract(data)

    def _extract(self, data: bytes) -> DocumentText:
        try:
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ProcessingError("ENCRYPTED_PDF")
            if not 1 <= len(reader.pages) <= 20:
                raise ProcessingError("PDF_PAGE_LIMIT")
            pages = []
            total = 0
            for number, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                total += len(text)
                if len(text) > 50_000 or total > 200_000 or "\x00" in text:
                    raise ProcessingError("PDF_TEXT_LIMIT")
                pages.append(TextPage(page=number, text=text))
            if not any(page.text.strip() for page in pages):
                raise ProcessingError("OCR_REQUIRED")
            return DocumentText(pages=pages)
        except ProcessingError:
            raise
        except SoftTimeLimitExceeded:
            raise ProcessingError("PROCESSING_TIMEOUT", True) from None
        except Exception:
            raise ProcessingError("INVALID_PDF") from None
