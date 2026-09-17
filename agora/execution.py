"""Agora 2.0 discussion↔execution converter.

The bridge that makes "discussion is execution" real:

    motion (adopted)  →  execution tasks (parents=[motion_id], so the
                         motion's conclusion is auto-injected as a parent
                         handoff into every worker's context)

    task (blocked)    →  motion (parents=[task_id], so the blocker's context
                         is auto-injected into the discussion)

Both directions lean on Hermes' parent handoff (``build_worker_context`` /
``_ctx_parent_results``) for context continuity — Agora writes no context-
shuttling code of its own.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from agora.kanban_compat import kanban_db as kb

logger = logging.getLogger(__name__)


def motion_to_tasks(
    conn,
    *,
    motion_id: str,
    action_items: list[Any],
    title: str,
    tenant: Optional[str] = None,
    assignee_map: Optional[dict[str, str]] = None,
) -> list[str]:
    """Create execution tasks from a motion's action items.

    Each task's parent is the motion task, so the motion's conclusion (written
    to ``task.result`` by close_motion) is auto-injected into the worker's
    context via parent handoff. ``action_items`` may be plain strings or dicts
    with ``{item, owner, depends_on}`` (matching 1.x's summary format).
    """
    if not action_items:
        return []
    assignee_map = assignee_map or {}
    created: dict[int, str] = {}
    out: list[str] = []

    for idx, ai in enumerate(action_items):
        if isinstance(ai, dict):
            item_title = str(ai.get("item") or ai).strip()
            owner = str(ai.get("owner") or "").strip()
            depends_on = ai.get("depends_on", []) or []
        else:
            item_title = str(ai).strip()
            owner = ""
            depends_on = []
        if not item_title:
            continue

        # Resolve owner → assignee (role name → worker profile), via map or
        # direct role name.
        assignee = assignee_map.get(owner, owner) if owner else None

        # Dependencies: numeric deps refer to previously created items.
        parent_ids: list[str] = []
        for dep in depends_on:
            dep_idx = dep - 1 if isinstance(dep, int) else None
            if dep_idx is not None and dep_idx in created:
                parent_ids.append(created[dep_idx])
        # Default parent is the motion itself — the context bridge.
        if not parent_ids:
            parent_ids = [motion_id]

        task_id = kb.create_task(
            conn,
            title=item_title[:200],
            body=(
                f"From Agora discussion: {title}\n"
                f"Motion: {motion_id}\n"
                f"Action item {idx + 1}/{len(action_items)}: {item_title}"
            ),
            assignee=assignee,
            workspace_kind="scratch",
            parents=parent_ids,
            tenant=tenant,
        )
        created[idx] = task_id
        out.append(task_id)
        logger.info("motion→task: %s (%s)", task_id, item_title[:60])

    return out


def blocked_to_motion(
    conn,
    *,
    task_id: str,
    chat_root_id: str,
    title: str = "",
    participants: Optional[list[str]] = None,
    chair: str = "",
    tenant: Optional[str] = None,
) -> str:
    """Raise a motion from a blocked task.

    The motion's parent is the blocked task, so the blocker's context (body,
    result, comments) is auto-injected into the discussion. Participants
    default to [blocked task's assignee, "architect", "leader"].
    """
    from agora import motion

    task = kb.get_task(conn, task_id)
    if task is None:
        raise ValueError(f"task {task_id} not found")
    if not title:
        title = f"Blocked: {task.title}"

    participants = participants or []
    if task.assignee and task.assignee not in participants:
        participants.insert(0, task.assignee)
    for default in ("architect", "leader"):
        if default not in participants:
            participants.append(default)

    motion_id = motion.create_motion(
        conn,
        chat_root_id=chat_root_id,
        title=title,
        description=task.body or task.title,
        participants=participants,
        chair=chair,
        source_task_id=task_id,
        project=(tenant or "").removeprefix("agora-"),
        tenant=tenant,
    )
    # Re-parent: motion depends on the blocked task for context handoff.
    kb.link_tasks(conn, parent_id=task_id, child_id=motion_id)
    logger.info("blocked→motion: task=%s motion=%s", task_id, motion_id)
    return motion_id
