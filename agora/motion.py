"""Agora 2.0 motion — structured discussion as a Kanban sub-task.

A motion is a child task hanging off the team chat root. The discussion body
(speech, votes, chair guidance) is written as ``[agora:msg]`` comments on that
task, and the conclusion lands in ``task.result`` when the motion completes.
This replaces Agora 1.x's separate ``motions.db`` — everything is on Kanban,
so the dashboard, notifier, and dispatcher see motions like any other task.

Encoding conventions (all prefixes mirror Hermes' swarm blackboard style):

    motion metadata   ``[agora:motion-meta] {json}``   (appended comment; last wins)
    speech            ``[agora:msg] {type: proposal|opinion, ...}``
    vote              ``[agora:msg] {type: vote, value: adopt|reject, ...}``
    conclusion        ``task.result`` = {decision, rationale, action_items}

The discussion scheduler (``next_speaker``) is deterministic and sparse:
round-robin across participants, with @mention targets jumping the queue.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Optional

from agora.kanban_compat import kanban_db as kb

logger = logging.getLogger(__name__)

MOTION_META_PREFIX = "[agora:motion-meta] "

# Speech stance values (kept for parity with 1.x message model).
_STANCES = {"support", "oppose", "neutral", "investigation"}


def create_motion(
    conn,
    *,
    chat_root_id: str,
    title: str,
    description: str = "",
    participants: Optional[list[str]] = None,
    chair: str = "",
    max_steps: int = 30,
    source_task_id: str = "",
    project: str = "",
    tenant: Optional[str] = None,
) -> str:
    """Create a motion sub-task under the chat root. Returns the task id."""
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    participants = participants or ["architect", "developer", "reviewer"]

    motion_id = kb.create_task(
        conn,
        title=f"[Motion] {title}",
        body=description or title,
        assignee=chair or None,
        parents=[chat_root_id],
        tenant=tenant,
        initial_status="running",  # discussing == running
    )

    _set_meta(
        conn, motion_id,
        participants=participants,
        chair=chair,
        max_steps=max_steps,
        state="discussing",
        step_count=0,
        source_task_id=source_task_id,
        project=project,
        chat_root_id=chat_root_id,
    )
    return motion_id


def _set_meta(conn, motion_id: str, **fields: Any) -> None:
    """Append one motion-metadata comment. Later comments win per key."""
    body = MOTION_META_PREFIX + json.dumps(fields, ensure_ascii=False)
    kb.add_comment(conn, motion_id, author="agora", body=body)


def get_motion_meta(conn, motion_id: str) -> dict[str, Any]:
    """Merge all motion-metadata comments (last-wins per key)."""
    merged: dict[str, Any] = {}
    for c in kb.list_comments(conn, motion_id):
        if not (c.body or "").startswith(MOTION_META_PREFIX):
            continue
        try:
            payload = json.loads(c.body[len(MOTION_META_PREFIX):])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict):
            merged.update(payload)
    return merged


def get_motion(conn, motion_id: str) -> Optional[dict[str, Any]]:
    """Return a motion view: task fields + metadata + speeches + votes."""
    task = kb.get_task(conn, motion_id)
    if task is None:
        return None
    meta = get_motion_meta(conn, motion_id)
    return {
        "id": motion_id,
        "title": (task.title or "").removeprefix("[Motion] "),
        "description": task.body or "",
        "status": task.status,
        "decision": _decision_from_result(task.result),
        "rationale": _rationale_from_result(task.result),
        "action_items": _action_items_from_result(task.result),
        "participants": meta.get("participants", []),
        "chair": meta.get("chair", ""),
        "max_steps": meta.get("max_steps", 30),
        "state": meta.get("state", "discussing"),
        "step_count": meta.get("step_count", 0),
        "source_task_id": meta.get("source_task_id", ""),
        "project": meta.get("project", ""),
        "chat_root_id": meta.get("chat_root_id", ""),
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    }


def list_motions(
    conn,
    *,
    chat_root_id: str,
    status_filter: str = "all",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List motions under a chat root (they are child tasks)."""
    child_ids = kb.child_ids(conn, chat_root_id)
    out: list[dict[str, Any]] = []
    for cid in child_ids:
        m = get_motion(conn, cid)
        if m is None:
            continue
        if status_filter == "active" and m["status"] in ("done", "archived"):
            continue
        if status_filter == "closed" and m["status"] != "done":
            continue
        out.append(m)
    return out[:limit]


def add_speech(
    conn,
    *,
    motion_id: str,
    role: str,
    round_num: int,
    stance: str,
    content: str,
    step_type: str = "speak",
) -> int:
    """Record one speech as a ``[agora:msg]`` proposal/opinion comment."""
    stance = stance if stance in _STANCES else "neutral"
    msg_type = "proposal" if step_type == "proposal" else "opinion"
    from agora import chat as _chat
    # Use chat.post_message with motion_id so the payload carries the motion.
    body = _chat.MSG_PREFIX + json.dumps({
        "type": msg_type,
        "role": role,
        "round": round_num,
        "stance": stance,
        "content": content,
        "motion": motion_id,
    }, ensure_ascii=False)
    return kb.add_comment(conn, motion_id, author=role, body=body)


def add_vote(
    conn,
    *,
    motion_id: str,
    role: str,
    vote: str,
    reason: str = "",
    confidence: float = 0.8,
) -> int:
    """Record one vote as a ``[agora:msg]`` vote comment."""
    from agora import chat as _chat
    body = _chat.MSG_PREFIX + json.dumps({
        "type": "vote",
        "role": role,
        "value": vote,
        "reason": reason,
        "confidence": confidence,
        "motion": motion_id,
    }, ensure_ascii=False)
    return kb.add_comment(conn, motion_id, author=role, body=body)


def get_speeches(conn, motion_id: str) -> list[dict[str, Any]]:
    """Parse speeches (proposal/opinion) from a motion's comments."""
    from agora import chat as _chat
    out: list[dict[str, Any]] = []
    for c in kb.list_comments(conn, motion_id):
        p = _chat.parse_message(c.body)
        if p is None or p.get("type") not in ("proposal", "opinion"):
            continue
        p["comment_id"] = c.id
        p["created_at"] = c.created_at
        out.append(p)
    return out


def get_votes(conn, motion_id: str) -> list[dict[str, Any]]:
    """Parse votes from a motion's comments."""
    from agora import chat as _chat
    out: list[dict[str, Any]] = []
    for c in kb.list_comments(conn, motion_id):
        p = _chat.parse_message(c.body)
        if p is None or p.get("type") != "vote":
            continue
        p["comment_id"] = c.id
        p["created_at"] = c.created_at
        out.append(p)
    return out


def close_motion(
    conn,
    *,
    motion_id: str,
    decision: str,
    rationale: str = "",
    action_items: Optional[list[str]] = None,
) -> None:
    """Write the conclusion into task.result and complete the motion task.

    Enforces the 1.x storage guard: 'adopted' requires at least one speech or
    vote — otherwise the decision is downgraded to 'error' so a never-discussed
    motion can never be marked adopted.
    """
    speeches = get_speeches(conn, motion_id)
    votes = get_votes(conn, motion_id)
    if decision == "adopted" and not speeches and not votes:
        logger.warning(
            "close_motion: rejected 'adopted' for motion %s (no speeches/votes) — downgraded to 'error'",
            motion_id,
        )
        decision = "error"
        if not rationale:
            rationale = "Motion closed as 'adopted' but had no discussion; downgraded by storage guard."

    result = json.dumps({
        "decision": decision,
        "rationale": rationale,
        "action_items": action_items or [],
    }, ensure_ascii=False)

    # Direct done-flip: the motion's parent is the chat root (pinned
    # 'scheduled', never done), so complete_task's parent-satisfied gate would
    # refuse. Discussions are driven by Agora, not the dispatcher, so bypass
    # complete_task and write status/result/completed_at directly.
    import time as _time
    now = int(_time.time())
    conn.execute(
        "UPDATE tasks SET status='done', result=?, completed_at=? WHERE id=?",
        (result, now, motion_id),
    )
    conn.commit()
    kb._append_event(conn, motion_id, "completed", {"decision": decision, "summary": rationale[:400] or None})

    _set_meta(conn, motion_id, state="closed")


def update_state(
    conn,
    *,
    motion_id: str,
    state: str,
    next_speaker: Optional[str] = None,
    last_guidance: Optional[str] = None,
    last_action: Optional[str] = None,
) -> None:
    """Update the discussion-state metadata."""
    fields: dict[str, Any] = {"state": state}
    if next_speaker is not None:
        fields["next_speaker"] = next_speaker
    if last_guidance is not None:
        fields["last_guidance"] = last_guidance
    if last_action is not None:
        fields["last_action"] = last_action
    _set_meta(conn, motion_id, **fields)


def get_state(conn, motion_id: str) -> dict[str, Any]:
    """Return the current discussion-state metadata."""
    meta = get_motion_meta(conn, motion_id)
    return {
        "state": meta.get("state", "discussing"),
        "next_speaker": meta.get("next_speaker"),
        "last_guidance": meta.get("last_guidance"),
        "last_action": meta.get("last_action"),
    }


def increment_step_count(conn, motion_id: str) -> int:
    """Increment and return the discussion step count."""
    meta = get_motion_meta(conn, motion_id)
    n = int(meta.get("step_count", 0)) + 1
    _set_meta(conn, motion_id, step_count=n)
    return n


def next_speaker(
    participants: list[str],
    *,
    last_speaker: Optional[str],
    history: Iterable[dict[str, Any]],
    mentions: Iterable[str] = (),
) -> str:
    """Deterministic discussion scheduler.

    Priority:
      1. An @mention target who has not yet spoken this round.
      2. Round-robin: the next participant after ``last_speaker``.

    Returns the chosen participant. Sparse — called only when a speaker is
    needed, not on every turn (unlike AutoGen's selector).
    """
    participants = [p for p in participants if p]
    if not participants:
        return ""
    spoken = {h.get("role") for h in history if h.get("role")}

    # 1. @mention priority.
    for m in mentions:
        if m in participants and m not in spoken:
            return m

    # 2. Round-robin.
    if last_speaker in participants:
        idx = participants.index(last_speaker)
        return participants[(idx + 1) % len(participants)]
    return participants[0]


def _decision_from_result(result: Optional[str]) -> str:
    return _result_field(result, "decision") or ""


def _rationale_from_result(result: Optional[str]) -> str:
    return _result_field(result, "rationale") or ""


def _action_items_from_result(result: Optional[str]) -> list[str]:
    items = _result_field(result, "action_items")
    return items if isinstance(items, list) else []


def _result_field(result: Optional[str], key: str) -> Any:
    if not result:
        return None
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed.get(key)
