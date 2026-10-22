from typing import Any, Literal

from pydantic import BaseModel

ErrorCode = Literal[
    "UNAUTHENTICATED",
    "FORBIDDEN",
    "NOT_FOUND",
    "VALIDATION_ERROR",
    "VERSION_CONFLICT",
    "STATE_CONFLICT",
    "IDEMPOTENCY_CONFLICT",
    "INVALID_CURSOR",
    "UPLOAD_INVALID",
    "DEPENDENCY_UNAVAILABLE",
    "DATABASE_UNAVAILABLE",
    "ADMISSION_LIMIT",
    "WEBHOOK_INVALID",
    "WEBHOOK_EXPIRED",
    "INTERNAL_ERROR",
]


class FieldError(BaseModel):
    path: str
    code: str
    message: str


class ApiProblem(BaseModel):
    type: str
    title: str
    status: int
    code: ErrorCode
    detail: str
    instance: str
    trace_id: str
    errors: list[FieldError] = []


class ApiError(Exception):
    def __init__(self, status: int, code: ErrorCode, detail: str) -> None:
        self.status, self.code, self.detail = status, code, detail
        super().__init__(code)


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {
        "model": ApiProblem,
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ApiProblem"}}
        },
        "description": title,
    }
    for status, title in {
        401: "Bearer credential missing, invalid or revoked",
        403: "Role is not permitted to perform this action",
        404: "Resource does not exist in the authenticated organization",
        409: "Version, state, uniqueness or idempotency conflict",
        413: "Request body exceeds the bounded API payload limit",
        422: "Schema, currency, accounting or cursor validation failed",
        429: ("Organization has reached its pending-job admission limit; Retry-After is returned"),
        500: "Unexpected failure; use trace_id when reporting the error",
        503: "Dependency unavailable; retry with the same Idempotency-Key",
    }.items()
}
