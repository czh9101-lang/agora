"""Agora 2.0 chat bus — the unified team channel, built directly on Kanban.

The chat root is a persistent Kanban task pinned in ``scheduled`` status
(never dispatchable, never completes). Messages are ``task_comments`` on the
root carrying an ``[agora:msg]`` JSON prefix; per-worker "what have I seen"
tracking reuses Hermes' notify-subscription cursors
(``kanban_db_notify``), with each worker subscribed under
``platform="agora", chat_id=<worker>`` so the gateway notifier silently skips
them and Agora owns the pull loop.

Message types (the ``type`` field inside the ``[agora:msg]`` payload):

    progress  — status-change point (global)
    blocking  — worker hit a blocker (global)
    mention   — @target ping (global; carries ``target``)
    proposal  — motion structured speech (motion-scoped)
    opinion   — motion structured speech (motion-scoped)
    vote      — motion structured speech (motion-scoped)
    decision  — motion conclusion (motion-scoped)

This module is the 2.0 replacement for Agora 1.x's separate motions.db — the
chat root IS the shared space, and motions/tasks hang off it as child tasks.

Note on ids: Hermes ``create_task`` never accepts a caller-chosen id (it mints
``t_<hex>``). The chat root is therefore created idempotently via
``idempotency_key`` and the returned real id is persisted by the caller
(project_planner) into the project JSON as ``chat_root_id``. This module only
ever *uses* a ``root_id`` it is given; ``ensure_chat_root`` returns the real id.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Optional

from agora.kanban_compat import kanban_db as kb

logger = logging.getLogger(__name__)

MSG_PREFIX = "[agora:msg] "
PLATFORM = "agora"  # custom notify platform — gateway notifier skips it, we own the pull

# Message types valid for a global post (motion-scoped types live in the
# motion module and are not posted to the global root directly).
_GLOBAL_TYPES = {"progress", "blocking", "mention"}

# All message types (global + motion-scoped), for validation.
_MESSAGE_TYPES = _GLOBAL_TYPES | {"proposal", "opinion", "vote", "decision"}


def ensure_chat_root(
    conn,
    *,
    project_name: str,
    tenant: Optional[str] = None,
    created_by: str = "agora",
) -> str:
    """Create (or return) the chat-root task for a project.

    Idempotent via ``idempotency_key``. Returns the REAL task id (a Hermes-
    minted ``t_<hex>``) — persist it as ``chat_root_id`` in the project JSON.

    The root is created ``blocked`` (create_task only accepts running/blocked
    as initial_status) and immediately parked to ``scheduled`` so the
    dispatcher never claims it and ``recompute_ready`` never promotes it.
    """
    from agora.kanban_compat import kanban_db as _kb

    root_id = _kb.create_task(
        conn,
        title=f"Agora team channel — {project_name}",
        body=(
            "Persistent team chat root. Messages are posted as structured "
            "comments with an `[agora:msg]` prefix; do not complete this task. "
            "Motions and execution tasks hang off this root as child tasks."
        ),
        assignee=created_by,
        tenant=tenant,
        initial_status="blocked",  # parked → scheduled right after
        idempotency_key=f"chat-root:{project_name}",
    )

    # Park the root so it is never dispatchable. schedule_task only fires when
    # the task is in a dispatchable state; a freshly-created blocked root
    # transitions blocked→scheduled.
    try:
        _kb.schedule_task(conn, root_id, reason="agora chat root (persistent anchor)")
    except Exception as exc:  # pragma: no cover — defensive; idempotency may return already-scheduled
        logger.debug("schedule_task on chat root %s skipped: %s", root_id, exc)

    # If the idempotency key already existed and was already scheduled, the
    # schedule_task above is a no-op — fine. Guarantee final state is scheduled.
    t = _kb.get_task(conn, root_id)
    if t is not None and t.status != "scheduled":
        conn.execute("UPDATE tasks SET status='scheduled' WHERE id=?", (root_id,))
        conn.commit()
    return root_id


def subscribe_worker(
    conn,
    *,
    root_id: str,
    worker: str,
) -> None:
    """Subscribe a worker to the chat root so it can track unseen messages."""
    kb.add_notify_sub(
        conn,
        task_id=root_id,
        platform=PLATFORM,
        chat_id=worker,
        thread_id="",
        user_id=worker,
        chat_type="agora-worker",
        delivery_mode="notify",  # never wakes a gateway adapter; we pull
    )


def unsubscribe_worker(conn, *, root_id: str, worker: str) -> None:
    kb.remove_notify_sub(
        conn,
        task_id=root_id,
        platform=PLATFORM,
        chat_id=worker,
        thread_id="",
    )


def post_message(
    conn,
    *,
    root_id: str,
    author: str,
    msg_type: str,
    content: str,
    target: Optional[str] = None,
    motion_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> int:
    """Post one structured message to the chat root. Returns the comment id."""
    if msg_type not in _MESSAGE_TYPES:
        raise ValueError(f"msg_type must be one of {sorted(_MESSAGE_TYPES)}")
    if msg_type == "mention" and not target:
        raise ValueError("mention requires a target")
    author = (author or "").strip()
    if not author:
        raise ValueError("author is required")
    content = (content or "").strip()
    if not content:
        raise ValueError("content is required")

    payload: dict[str, Any] = {
        "type": msg_type,
        "author": author,
        "content": content,
    }
    if target:
        payload["target"] = target
    if motion_id:
        payload["motion"] = motion_id
    if task_id:
        payload["task"] = task_id

    body = MSG_PREFIX + json.dumps(payload, ensure_ascii=False)
    return kb.add_comment(conn, root_id, author=author, body=body)


def parse_message(body: str) -> Optional[dict[str, Any]]:
    """Return the parsed ``[agora:msg]`` payload, or None if not a chat message."""
    body = (body or "").strip()
    if not body.startswith(MSG_PREFIX):
        return None
    try:
        payload = json.loads(body[len(MSG_PREFIX):])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def list_messages(conn, *, root_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """List parsed chat messages (most recent last), skipping non-message comments."""
    out: list[dict[str, Any]] = []
    for comment in kb.list_comments(conn, root_id):
        payload = parse_message(comment.body)
        if payload is None:
            continue
        payload["comment_id"] = comment.id
        payload["created_at"] = comment.created_at
        out.append(payload)
    return out[-limit:]


def pull_unseen(
    conn,
    *,
    root_id: str,
    worker: str,
) -> tuple[int, int, int]:
    """Atomically claim the worker's unseen message events on the chat root.

    Returns ``(old_cursor, new_cursor, new_message_count)``. The cursor advances
    inside the claim so concurrent pullers serialize on SQLite's writer lock
    and only the first sees a given event range. Call :func:`rewind_unseen`
    after a delivery failure to retry.
    """
    old, new, events = kb.claim_unseen_events_for_sub(
        conn,
        task_id=root_id,
        platform=PLATFORM,
        chat_id=worker,
        thread_id="",
        kinds=["commented"],
    )
    return old, new, len(events)


def read_recent(conn, *, root_id: str, worker: str, limit: int = 20) -> list[dict[str, Any]]:
    """Read the most recent chat messages (append-only, so recent == new)."""
    return list_messages(conn, root_id=root_id, limit=limit)


def rewind_unseen(conn, *, root_id: str, worker: str, old_cursor: int, claimed_cursor: int) -> None:
    """Roll a worker's cursor back so unseen events can be re-claimed.

    ``old_cursor`` is the cursor BEFORE the failed claim; ``claimed_cursor``
    is what the claim advanced it to. The CAS guard only rewinds if no later
    notifier moved the row, so a retry never clobbers newer progress.
    """
    kb.rewind_notify_cursor(
        conn,
        task_id=root_id,
        platform=PLATFORM,
        chat_id=worker,
        thread_id="",
        claimed_cursor=claimed_cursor,
        old_cursor=old_cursor,
    )
