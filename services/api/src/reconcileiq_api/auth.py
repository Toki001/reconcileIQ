import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from reconcileiq_database.models import Credential, Membership, User
from sqlalchemy.orm import Session

from reconcileiq_api.errors import ApiError


class Role(StrEnum):
    ADMIN = "admin"
    REVIEWER = "reviewer"
    AUDITOR = "auditor"


@dataclass(frozen=True)
class Principal:
    organization_id: str
    actor: str
    role: Role
    user_id: str | None = None


bearer = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description=(
        "Opaque organization-scoped API key. Server-side digest lookup supplies actor and role. "
        "Keys are never accepted in query parameters. Local bootstrap provisions "
        "admin, reviewer and auditor keys."
    ),
)


def authenticate(
    request: Request,
    credential: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credential is None or len(credential.credentials) < 32 or len(credential.credentials) > 256:
        raise ApiError(401, "UNAUTHENTICATED", "A valid bearer credential is required.")
    digest = hashlib.sha256(credential.credentials.encode()).hexdigest()
    with Session(request.app.state.engine) as session:
        row = session.get(Credential, digest)
        if row is None or row.revoked or row.role not in Role:
            raise ApiError(401, "UNAUTHENTICATED", "A valid bearer credential is required.")
        if row.user_id is not None:
            user = session.get(User, row.user_id)
            membership = session.get(Membership, (row.organization_id, row.user_id))
            if user is None or not user.active or membership is None or not membership.active:
                raise ApiError(401, "UNAUTHENTICATED", "A valid bearer credential is required.")
        return Principal(row.organization_id, row.actor, Role(row.role), row.user_id)


Actor = Annotated[Principal, Depends(authenticate)]


def require_writer(principal: Principal, *, admin: bool = False) -> None:
    if principal.role == Role.AUDITOR or (admin and principal.role != Role.ADMIN):
        raise ApiError(403, "FORBIDDEN", "The authenticated role cannot perform this action.")
