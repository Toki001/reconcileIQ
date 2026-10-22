import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from reconcileiq_database.config import Settings
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from reconcileiq_api.auth import Principal
from reconcileiq_api.contracts import PageInfo
from reconcileiq_api.errors import ApiError


@dataclass
class Page:
    rows: list[Any]
    cursors: list[str]
    info: PageInfo


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def paginate(
    session: Session,
    model: Any,
    actor: Principal,
    settings: Settings,
    *,
    first: int = 20,
    after: str | None = None,
    scope: str,
    conditions: list[Any] | None = None,
    time_field: str = "created_at",
) -> Page:
    if type(first) is not int or not 1 <= first <= 100:
        raise ApiError(422, "VALIDATION_ERROR", "first must be between 1 and 100.")
    secret = settings.cursor_secret.get_secret_value()
    if len(secret) < 32:
        raise ApiError(503, "DEPENDENCY_UNAVAILABLE", "Pagination signing key is not configured.")
    field = getattr(model, time_field)
    identity = hashlib.sha256(json.dumps([actor.organization_id, scope]).encode()).hexdigest()
    query = select(model).where(model.organization_id == actor.organization_id, *(conditions or []))
    lower: tuple[datetime, str] | None = None
    upper: tuple[datetime, str] | None = None
    if after is not None:
        try:
            if len(after) > 2048:
                raise ValueError()
            raw = base64.b64decode(after.encode(), altchars=b"-_", validate=True)
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(signature, hmac.digest(secret.encode(), payload, "sha256")):
                raise ValueError()
            data = json.loads(payload)
            if data["v"] != 1 or data["scope"] != identity:
                raise ValueError()
            lower = datetime.fromisoformat(data["after"][0]), data["after"][1]
            upper = datetime.fromisoformat(data["upper"][0]), data["upper"][1]
        except (ValueError, KeyError, TypeError, IndexError):
            raise ApiError(
                422, "INVALID_CURSOR", "Cursor is malformed or belongs to another query."
            ) from None
    else:
        top = session.scalar(query.order_by(field.desc(), model.id.desc()).limit(1))
        if top is not None:
            upper = utc(getattr(top, time_field)), top.id
    if upper is None:
        return Page([], [], PageInfo(has_next_page=False, end_cursor=None))
    query = query.where(or_(field < upper[0], and_(field == upper[0], model.id <= upper[1])))
    if lower:
        query = query.where(or_(field > lower[0], and_(field == lower[0], model.id > lower[1])))
    found: list[Any] = list(session.scalars(query.order_by(field, model.id).limit(first + 1)))
    cursors = []
    for row in found[:first]:
        payload = json.dumps(
            {
                "v": 1,
                "scope": identity,
                "after": [utc(getattr(row, time_field)).isoformat(), row.id],
                "upper": [upper[0].isoformat(), upper[1]],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        cursors.append(
            base64.urlsafe_b64encode(
                payload + hmac.digest(secret.encode(), payload, "sha256")
            ).decode()
        )
    return Page(
        found[:first],
        cursors,
        PageInfo(has_next_page=len(found) > first, end_cursor=cursors[-1] if cursors else None),
    )
