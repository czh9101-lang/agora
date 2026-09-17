"""Kanban-backed compatibility layer for the 1.x motions storage API.

This module exposes the same function signatures as ``agora.storage.motions``
but persists everything on the Kanban board via ``agora.motion`` — a motion is
a sub-task of the team chat root, speech/votes are ``[agora:msg]`` comments,
and the conclusion lands in ``task.result``.

Purpose: let the 1.x discussion engine (``agora.discussion.driver``) and other
callers switch to the 2.0 Kanban backend by changing a single import, while the
field names they read stay stable. Field-name differences (e.g. 1.x ``vote`` vs
``agora.motion`` ``value``) are mapped here, not in the callers.

Unlike ``agora.motion`` (which takes an explicit ``conn``), this module opens a
fresh connection per call, matching the 1.x ``motions`` module's behaviour so
every existing call site keeps working unchanged.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _conn():
    from agora.kanban_compat import kanban_db as _kb
    return _kb.connect()


def _resolve_chat_root(chat_root_id: str, project: str) -> str:
    """Return a chat root id, resolving from the project JSON when not given."""
    if chat_root_id:
        return chat_root_id
    if not project:
        raise ValueError("chat_root_id or project is required to locate the team channel")
    from project_planner import get_project
    proj = get_project(project)
    if not proj:
        raise ValueError(f"project '{project}' not found")
    root = proj.get("chat_root_id", "")
    if not root:
        raise ValueError(f"project '{project}' has no chat channel yet (start it first)")
    return root


def get_motion(motion_id: str) -> Optional[dict[str, Any]]:
    """Fetch a motion view in the 1.x field shape (discussing/closed status)."""
    from agora import motion as _m
    conn = _conn()
    try:
        view = _m.get_motion(conn, motion_id)
        if view is None:
            return None
        return _to_legacy_motion(view)
    finally:
        conn.close()


def _to_legacy_motion(view: dict[str, Any]) -> dict[str, Any]:
    """Map a 2.0 kanban motion view onto the 1.x motions field shape.

    Callers of the 1.x API read ``status`` as ``discussing``/``closed``,
    ``current_round``/``max_rounds`` for round counts, and ``source`` — all of
    which are absent or differently named on the Kanban backend. This shim
    restores those names so every legacy call site keeps working.
    """
    kanban_status = view.get("status", "todo")
    legacy_status = "closed" if kanban_status == "done" else "discussing"
    step_count = view.get("step_count", 0) or 0
    return {
        **view,
        "status": legacy_status,
        "current_round": step_count,
        "max_rounds": view.get("max_steps", 30),
        "source": view.get("source", "agent"),
        "source_profile": view.get("source_profile"),
        "blocking": bool(view.get("blocking", False)),
        "closed_at": view.get("completed_at"),
        "state": view.get("state", "discussing"),
    }


def create_motion(
    title: str,
    description: str = "",
    max_rounds: int = 3,
    source: str = "user",
    source_task_id: str = "",
    blocking: bool = False,
    participants: Optional[list[str]] = None,
    chair: str = "",
    max_steps: int = 30,
    project: str = "",
    chat_root_id: str = "",
    tenant: Optional[str] = None,
) -> dict:
    """Create a motion sub-task under the chat root. Returns the motion view.

    ``chat_root_id`` is required (the team channel anchor); callers that only
    know the project name should resolve it first (project JSON ``chat_root_id``).
    """
    from agora import motion as _m
    chat_root_id = _resolve_chat_root(chat_root_id, project)
    conn = _conn()
    try:
        mid = _m.create_motion(
            conn,
            chat_root_id=chat_root_id,
            title=title,
            description=description,
            participants=participants,
            chair=chair,
            max_steps=max_steps,
            source_task_id=source_task_id,
            project=project,
            tenant=tenant,
        )
        view = _m.get_motion(conn, mid)
        if view is None:  # pragma: no cover — create_motion guarantees existence
            raise RuntimeError(f"created motion {mid} but could not read it back")
        return view
    finally:
        conn.close()


def list_motions(
    status_filter: str = "all",
    limit: int = 20,
    project: str = "",
    chat_root_id: str = "",
) -> list[dict[str, Any]]:
    """List motions under a chat root. ``chat_root_id`` resolved from project if omitted.

    When neither ``chat_root_id`` nor ``project`` is given, aggregates across
    all active projects (the 1.x "global list" behaviour for CLI/dashboard).
    """
    from agora import motion as _m
    if not (chat_root_id or project):
        return list_all_motions(status_filter=status_filter, limit=limit)
    chat_root_id = _resolve_chat_root(chat_root_id, project)
    conn = _conn()
    try:
        views = _m.list_motions(conn, chat_root_id=chat_root_id, status_filter=status_filter, limit=limit)
        return [_to_legacy_motion(v) for v in views]
    finally:
        conn.close()


def list_all_motions(status_filter: str = "all", limit: int = 100) -> list[dict[str, Any]]:
    """List motions across all active projects (for CLI/dashboard global views)."""
    from agora import motion as _m
    try:
        from project_planner import list_projects
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    conn = _conn()
    try:
        for proj in list_projects():
            root = (proj or {}).get("chat_root_id", "")
            if not root:
                continue
            for v in _m.list_motions(conn, chat_root_id=root, status_filter=status_filter, limit=limit):
                if v["id"] in seen:
                    continue
                seen.add(v["id"])
                out.append(_to_legacy_motion(v))
    finally:
        conn.close()
    return out[:limit]


def update_motion_status(
    motion_id: str,
    status: str,
    decision: str = "",
    rationale: str = "",
    action_items: Optional[list[str]] = None,
) -> None:
    """Close a motion (only closing is supported on the Kanban backend)."""
    from agora import motion as _m
    if status != "closed":
        # Kanban motions are either discussing (running) or done; there is no
        # intermediate status to persist. Ignore non-closing transitions.
        return
    conn = _conn()
    try:
        _m.close_motion(conn, motion_id=motion_id, decision=decision, rationale=rationale, action_items=action_items)
    finally:
        conn.close()


def update_motion_state(motion_id: str, state: str) -> None:
    """Update the motion's discussion state (discussing/voting/summarizing/closed)."""
    from agora import motion as _m
    conn = _conn()
    try:
        _m.update_state(conn, motion_id=motion_id, state=state)
    finally:
        conn.close()


def save_discussion_state(
    motion_id: str,
    current_state: str,
    next_speaker: Optional[str] = None,
    last_guidance: Optional[str] = None,
    last_action: Optional[str] = None,
) -> None:
    """Persist the discussion state (next speaker, guidance, action)."""
    from agora import motion as _m
    conn = _conn()
    try:
        _m.update_state(
            conn,
            motion_id=motion_id,
            state=current_state,
            next_speaker=next_speaker,
            last_guidance=last_guidance,
            last_action=last_action,
        )
    finally:
        conn.close()


def get_discussion_state(motion_id: str) -> Optional[dict[str, Any]]:
    """Return the current discussion state, or None if the motion is gone."""
    from agora import motion as _m
    conn = _conn()
    try:
        m = _m.get_motion(conn, motion_id)
        if m is None:
            return None
        state = _m.get_state(conn, motion_id)
        # 1.x returned ``current_state``; map the field name.
        return {
            "current_state": state.get("state", "discussing"),
            "next_speaker": state.get("next_speaker"),
            "last_guidance": state.get("last_guidance"),
            "last_action": state.get("last_action"),
        }
    finally:
        conn.close()


def increment_step_count(motion_id: str) -> int:
    """Increment and return the discussion step count."""
    from agora import motion as _m
    conn = _conn()
    try:
        return _m.increment_step_count(conn, motion_id)
    finally:
        conn.close()


def add_message(
    motion_id: str,
    role: str,
    round_num: int,
    stance: str,
    content: str,
    step_type: str = "speak",
    target_role: str = "",
    is_chair: bool = False,
) -> str:
    """Record one discussion message (speech, guidance, vote call, …)."""
    from agora import motion as _m
    conn = _conn()
    try:
        cid = _m.add_speech(
            conn,
            motion_id=motion_id,
            role=role,
            round_num=round_num,
            stance=stance,
            content=content,
            step_type=step_type,
            target_role=target_role,
            is_chair=is_chair,
        )
        return str(cid)
    finally:
        conn.close()


def get_messages(motion_id: str, round_num: Optional[int] = None) -> list[dict[str, Any]]:
    """Return all discussion messages (1.x shape: role/is_chair/step_type/content)."""
    from agora import motion as _m
    conn = _conn()
    try:
        msgs = _m.get_all_messages(conn, motion_id)
    finally:
        conn.close()
    if round_num is not None:
        msgs = [m for m in msgs if m.get("round") == round_num]
    # 1.x used ``round_num``; the driver reads ``step_type``/``is_chair``/``role``/``content``.
    out = []
    for m in msgs:
        out.append({
            "id": str(m.get("comment_id", "")),
            "motion_id": motion_id,
            "role": m.get("role", ""),
            "round_num": m.get("round", 0),
            "stance": m.get("stance", "neutral"),
            "content": m.get("content", ""),
            "step_type": m.get("step_type", "speak"),
            "target_role": m.get("target_role", ""),
            "is_chair": bool(m.get("is_chair", False)),
            "timestamp": m.get("created_at"),
        })
    return out


def add_vote(
    motion_id: str,
    role: str,
    vote: str,
    reason: str = "",
    confidence: float = 0.8,
) -> str:
    """Record one vote."""
    from agora import motion as _m
    conn = _conn()
    try:
        cid = _m.add_vote(conn, motion_id=motion_id, role=role, vote=vote, reason=reason, confidence=confidence)
        return str(cid)
    finally:
        conn.close()


def get_votes(motion_id: str) -> list[dict[str, Any]]:
    """Return all votes (1.x shape: role/vote/reason)."""
    from agora import motion as _m
    conn = _conn()
    try:
        votes = _m.get_votes(conn, motion_id)
    finally:
        conn.close()
    return [
        {
            "id": str(v.get("comment_id", "")),
            "motion_id": motion_id,
            "role": v.get("role", ""),
            "vote": v.get("value", v.get("vote", "")),
            "reason": v.get("reason", ""),
            "confidence": v.get("confidence", 0.8),
            "timestamp": v.get("created_at"),
        }
        for v in votes
    ]
