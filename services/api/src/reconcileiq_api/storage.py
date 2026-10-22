import hashlib
from datetime import timedelta
from io import BytesIO
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from minio import Minio
from minio.error import S3Error
from reconcileiq_database.config import Settings
from urllib3 import PoolManager, Timeout

from reconcileiq_api.errors import ApiError


class Storage(Protocol):
    def grant(self, key: str) -> str: ...
    def verify_and_seal(self, key: str, organization_id: str, size: int, digest: str) -> str: ...


class MinioStorage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def client(self, *, public: bool = False) -> Minio:
        endpoint = urlsplit(
            str(self.settings.minio_public_endpoint if public else self.settings.minio_endpoint)
        )
        if (
            not self.settings.minio_access_key.get_secret_value()
            or not self.settings.minio_secret_key.get_secret_value()
        ):
            raise ApiError(
                503, "DEPENDENCY_UNAVAILABLE", "Object storage credentials are not configured."
            )
        return Minio(
            endpoint.netloc,
            access_key=self.settings.minio_access_key.get_secret_value(),
            secret_key=self.settings.minio_secret_key.get_secret_value(),
            secure=endpoint.scheme == "https",
            region="us-east-1",
            http_client=PoolManager(timeout=Timeout(connect=3, read=10), retries=False),
        )

    def grant(self, key: str) -> str:
        try:
            client = self.client()
            if not client.bucket_exists(self.settings.minio_bucket):
                try:
                    client.make_bucket(self.settings.minio_bucket)
                except S3Error as exc:
                    if exc.code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                        raise
            return self.client(public=True).presigned_put_object(
                self.settings.minio_bucket, key, expires=timedelta(minutes=10)
            )
        except ApiError:
            raise
        except Exception:
            raise ApiError(
                503, "DEPENDENCY_UNAVAILABLE", "Object storage is unavailable."
            ) from None

    def verify_and_seal(self, key: str, organization_id: str, size: int, digest: str) -> str:
        try:
            client = self.client()
            stat = client.stat_object(self.settings.minio_bucket, key)
            if stat.size != size or stat.content_type != "application/pdf":
                raise ApiError(
                    422, "UPLOAD_INVALID", "Uploaded object metadata does not match the request."
                )
            response = client.get_object(self.settings.minio_bucket, key)
            try:
                data = response.read(size + 1)
            finally:
                response.close()
                response.release_conn()
            if (
                len(data) != size
                or not data.startswith(b"%PDF-")
                or hashlib.sha256(data).hexdigest() != digest
            ):
                raise ApiError(
                    422, "UPLOAD_INVALID", "Uploaded PDF size, signature or SHA-256 does not match."
                )
            # Presigned PUTs remain valid until expiry. Workers only receive the
            # sealed copy, so overwriting the upload URL cannot change verified input.
            sealed = f"{organization_id}/sealed/{uuid4().hex}.pdf"
            client.put_object(
                self.settings.minio_bucket,
                sealed,
                BytesIO(data),
                len(data),
                content_type="application/pdf",
            )
            return sealed
        except ApiError:
            raise
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                raise ApiError(
                    409, "STATE_CONFLICT", "Upload the object before completing the request."
                ) from None
            raise ApiError(
                503, "DEPENDENCY_UNAVAILABLE", "Object storage is unavailable."
            ) from None
        except Exception:
            raise ApiError(
                503, "DEPENDENCY_UNAVAILABLE", "Object storage is unavailable."
            ) from None
