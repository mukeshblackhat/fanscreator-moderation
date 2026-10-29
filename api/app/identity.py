"""Caller identity from X-User-Id and X-Workspace-Id. No login for the test task.

Rules:
* missing either header -> 401
* user not a member of that workspace -> 404 on media reads (do not leak
  existence), 403 on review actions
"""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Membership, Workspace


@dataclass(frozen=True)
class Caller:
    user_id: str
    workspace_id: str
    role: str | None          # None when the user is not a member of the workspace
    workspace_verified: bool

    @property
    def is_member(self) -> bool:
        return self.role is not None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


async def get_caller(
    x_user_id: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Caller:
    if not x_user_id or not x_workspace_id:
        raise HTTPException(status_code=401, detail="X-User-Id and X-Workspace-Id headers are required")
    row = (
        await session.execute(
            select(Membership.role, Workspace.verified)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(Membership.user_id == x_user_id, Membership.workspace_id == x_workspace_id)
        )
    ).first()
    if row is None:
        return Caller(x_user_id, x_workspace_id, None, False)
    return Caller(x_user_id, x_workspace_id, row.role, row.verified)


async def require_member(caller: Caller = Depends(get_caller)) -> Caller:
    """For media reads and uploads. Non-members get 404, never 403."""
    if not caller.is_member:
        raise HTTPException(status_code=404, detail="Not found")
    return caller


async def require_admin(caller: Caller = Depends(get_caller)) -> Caller:
    """For review actions. Members and non-members get 403."""
    if not caller.is_admin:
        raise HTTPException(status_code=403, detail="Only workspace admins can review media")
    return caller
