"""Runtime config helpers for LangGraph agents.

The frontend passes the authenticated user as a single object under
``configurable.user`` (camelCase fields from the Next.js session):

    configurable = {
        "model_name": "openai",
        "user": {
            "id": "<uuid>",
            "email": "...",
            "name": "...",
            "role": "notario",
            "companyId": "<uuid>",
            "isActive": True,
            "isCreator": True,
        },
    }

Old fallback: individual header-derived keys (x-user-id, x-company-id, …)
are still supported so legacy runs keep working.

Public API
----------
get_user()          -> UserContext   (raises RuntimeError if user not found)
get_thread_id()     -> str | None
get_event_message() -> str | None
get_user_info_by_id(user_id, company_id) -> dict  (Supabase enrichment)
"""

from __future__ import annotations

import os
from typing import Any, Optional

from pydantic import BaseModel, Field, ConfigDict
from langgraph.config import get_config
from langgraph.graph.state import RunnableConfig


# ── User model ─────────────────────────────────────────────────────────────

class UserContext(BaseModel):
    """Authenticated user extracted from LangGraph runtime config.

    Accepts both the new camelCase frontend format (``companyId``, ``isActive``,
    ``isCreator``) and the snake_case equivalents (``company_id``, …).
    """
    model_config = ConfigDict(populate_by_name=True)

    id: str
    email: str = ""
    name: str = ""
    role: str = "oficial"
    company_id: Optional[str] = Field(None, alias="companyId")
    is_active: bool = Field(True, alias="isActive")
    is_creator: bool = Field(False, alias="isCreator")


# ── Core helpers ────────────────────────────────────────────────────────────

def get_user() -> UserContext:
    """Return the authenticated user from the current LangGraph config.

    Resolution order:
    1. ``configurable.user``  – full object sent by the frontend (preferred)
    2. Legacy header keys     – ``x-user-id``, ``x-user-name``, …

    Raises:
        RuntimeError: if no user identity can be found in the config.
    """
    try:
        config: RunnableConfig = get_config()
        configurable = config.get("configurable", {})

        # ── Path 1: new full user object ─────────────────────────────────
        user_obj = configurable.get("user")
        if isinstance(user_obj, dict) and user_obj.get("id"):
            return UserContext.model_validate(user_obj)

        # ── Path 2: legacy header-derived keys ───────────────────────────
        user_id = configurable.get("x-user-id")
        if user_id:
            return UserContext(
                id=str(user_id),
                name=configurable.get("x-user-name") or "",
                email=configurable.get("x-user-email") or "",
                role=configurable.get("x-user-role") or "oficial",
                company_id=configurable.get("x-company-id") or None,
            )

    except Exception as e:
        print(f"[get_user] Exception: {e}", flush=True)
        import traceback
        traceback.print_exc()

    raise RuntimeError("User not found in LangGraph config (configurable.user or x-user-id missing)")


def get_thread_id() -> Optional[str]:
    """Return the current thread ID from LangGraph config."""
    try:
        config: RunnableConfig = get_config()
        return config.get("configurable", {}).get("thread_id")
    except Exception:
        return None


def get_event_message() -> Optional[str]:
    """Return the event_message from config (used by the triage agent)."""
    try:
        config: RunnableConfig = get_config()
        configurable = config.get("configurable", {})
        metadata = config.get("metadata", {})
        value = configurable.get("event_message") or metadata.get("event_message")
        return value if value else None
    except Exception:
        return None


def get_workspace_id(runtime: Optional[Any] = None) -> Optional[str]:
    """Return the current workspace/ticket path key for backends and file tools.

    If runtime has .context with workspace_id set (e.g. after seleccionar_ticket),
    returns that. Otherwise returns get_thread_id() so default behavior is unchanged.
    """
    if runtime is not None:
        ctx = getattr(runtime, "context", None)
        if ctx is not None:
            wid = getattr(ctx, "workspace_id", None)
            if wid:
                return wid
    return get_thread_id()


# ── Backward-compatible shims (kept so existing callers don't break) ────────

def get_user_data_from_config() -> dict:
    """Deprecated – use ``get_user()`` instead."""
    try:
        u = get_user()
        return {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "role": u.role,
            "company_id": u.company_id,
        }
    except RuntimeError:
        return {}


def get_user_id_from_config() -> Optional[str]:
    """Deprecated – use ``get_user().id`` instead."""
    try:
        return get_user().id
    except RuntimeError:
        # Final fallback: thread metadata
        try:
            config: RunnableConfig = get_config()
            return config.get("metadata", {}).get("user_id") or None
        except Exception:
            return None


def get_company_id_from_config() -> Optional[str]:
    """Deprecated – use ``get_user().company_id`` instead."""
    try:
        return get_user().company_id
    except RuntimeError:
        return None


def get_thread_id_from_config() -> Optional[str]:
    """Deprecated – use ``get_thread_id()`` instead."""
    return get_thread_id()


def get_event_message_from_config() -> Optional[str]:
    """Deprecated – use ``get_event_message()`` instead."""
    return get_event_message()


def get_user_from_config(config: RunnableConfig) -> dict:
    """Deprecated – use ``get_user()`` instead."""
    u = get_user()
    thread_id = config.get("metadata", {}).get("thread_id")
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "company_id": u.company_id,
        "conversation_id": thread_id,
    }


# ── Supabase enrichment (unchanged) ─────────────────────────────────────────

async def get_user_info_by_id(user_id: str, company_id: Optional[str] = None) -> dict:
    """Fetch enriched user info from Supabase by user_id.

    Queries the ``clients`` table for contact details (full_name, phone, address)
    and optionally the ``companies`` table for the company name.
    """
    from supabase import create_async_client

    supabase_url = os.getenv("SUPABASE_URL", "")
    supabase_key = os.getenv("SUPABASE_SECRET_KEY", "")

    result: dict = {
        "full_name": "",
        "email": "",
        "phone": "",
        "address": "",
        "company_name": "",
    }

    try:
        supabase = await create_async_client(supabase_url, supabase_key)

        client_resp = (
            await supabase.table("clients")
            .select("full_name, email, phone, address")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if client_resp.data:
            record = client_resp.data[0]
            result["full_name"] = record.get("full_name") or ""
            result["email"] = record.get("email") or ""
            result["phone"] = record.get("phone") or ""
            result["address"] = record.get("address") or ""

        if company_id:
            company_resp = (
                await supabase.table("companies")
                .select("name")
                .eq("id", company_id)
                .limit(1)
                .execute()
            )
            if company_resp.data:
                result["company_name"] = company_resp.data[0].get("name") or ""

    except Exception as e:
        print(f"[get_user_info_by_id] Exception: {e}", flush=True)
        import traceback
        traceback.print_exc()

    return result
