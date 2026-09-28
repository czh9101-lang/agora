---
name: agora-setup
description: Set up an Agora multi-agent team from scratch — create workers, form teams, start projects. Read this when the user wants to use Agora for collaborative development.
category: collaboration
version: 1.0.0
---

# Agora Setup — From Zero to Running Team

Use this skill when the user wants to set up an Agora multi-agent team for
collaborative software development. This covers the full setup flow: creating
workers, forming a team, and starting a self-driving project.

## Prerequisites

- Hermes Agent installed and running (v0.20.x or newer recommended)
- Agora plugin enabled (`hermes plugins enable agora` + restart **both** gateway and dashboard)
- Bundled skills deployed — `hermes agora setup` (also happens automatically on
  `agora_start_project`; installing the plugin itself writes nothing)
- A model/provider configured — each worker profile copies the global `config.yaml` and `.env` at creation
- The gateway running — it hosts the kanban dispatcher that spawns workers

## Quick Start (3 Steps)

### Step 1: Create Workers

Create workers from role templates. Each worker is a Hermes profile with its
own SOUL.md (identity) and skills directory. (Workers have no `memory` tool —
self-growth happens through **Skills** and **SOUL.md** only.)

**Worker toolsets (v1.8.6+):** `terminal, file, web, skills, todo, session_search`.
No `browser`, `tts`, `vision`, `code_execution`, `memory`, or `cronjob`.
Leader toolset: `file, web, skills, todo, session_search` (no `terminal`) + `agora` (overridden at spawn).

```
# List available templates
agora_list_templates()

# Create a leader (required — chairs discussions, runs heartbeat)
agora_create_worker(name="leader", role="leader")

# Create team members (pick roles relevant to your project)
agora_create_worker(name="developer", role="developer")
agora_create_worker(name="architect", role="architect")
agora_create_worker(name="tester", role="tester")
```

**Available templates:**

| Role | Best For |
|------|----------|
| `leader` | Project management, discussion chair, heartbeat (required) |
| `architect` | System design, API contracts, tech selection |
| `developer` | Implementation, testing, dependencies |
| `reviewer` | Code review, security, edge cases |
| `tester` | Test strategy, automation, bug reporting |
| `devops` | CI/CD, deployment, infrastructure |
| `researcher` | Web research, trend analysis, information synthesis |
| `writer` | Documentation, content, README |

**Tips:**
- Worker names become Hermes profile names. Use simple lowercase names.
- You need at least a `leader` + 2 workers for meaningful discussions.
- Workers persist across projects — create them once, reuse forever.
- Workers can be assigned any model via Dashboard → Profiles → Config.

### Step 2: Form a Team

```
agora_create_team(
    team_name="alpha",
    workers=["leader", "architect", "developer", "tester"],
)
```

A team is the assignee pool for a project. The same worker can be on
multiple teams.

### Step 3: Start a Project

```
agora_start_project(
    name="my-project",
    workdir="/path/to/project/repo",
    goal="Build a REST API with authentication and pagination",
    stop_condition="All endpoints tested and documented",
    heartbeat_member="leader",   # who wakes on heartbeat
    heartbeat_minutes=30,        # heartbeat interval
    allow_unattended=True,       # let workers write code (see below)
)
```

That's it. The leader will wake up on the next heartbeat, read AGENTS.md
for project context, and start working autonomously.

> **`allow_unattended` — decide this before starting.** Workers and the leader
> run as `hermes -p <profile> chat -Q -q` subprocesses. A `-q` invocation has
> nobody present to answer an approval prompt, so **without this flag their
> flagged actions fail closed**: they can read, search, discuss and plan, but
> cannot write files or run commands — a team that only talks.
>
> Passing `allow_unattended=True` adds `--yolo --accept-hooks` to every worker
> and leader subprocess for that project. Do it only for a workdir you are
> willing to let agents modify unattended, and say so when you set it up.
> Default is `False`; the flag is stored on the project (`allow_unattended`) and
> can be enabled later by re-running `agora_start_project` for that name with
> `allow_unattended=True`.

## What Happens Next

1. **Heartbeat fires** → leader wakes, reads AGENTS.md, checks kanban
2. **Leader plans** → creates tasks, assigns to workers via kanban
3. **Workers execute** → dispatcher spawns workers for each task
4. **Discussions** → leader raises motions for design decisions, workers debate
5. **Self-stop** → when stop condition is met, leader raises a motion to vote

> **Set expectations:** the leader is only woken at heartbeat time (default 15
> minutes, `heartbeat_minutes`). Nothing happening right after `start_project`
> is normal — it is not a failure. Tell the user this up front, or lower the
> interval for a quick first look.

## Monitoring

```
# Check project status
agora_project_status(name="my-project")

# List active discussions
agora_list_motions(status="active")

# Get discussion result
agora_get_result(motion_id="t_xxx")

# Read discussion messages
agora_get_messages(motion_id="t_xxx")

# Team channel — what workers reported to each other (2.0)
agora_read_chat(project="my-project", limit=20)
```

Or open the Dashboard: `hermes dashboard` → Agora tab.

## Updating Projects Mid-Flight

Change direction without stopping:

```
agora_update_project(
    name="my-project",
    goal="Pivot to GraphQL API",
    stop_condition="GraphQL schema complete and tested",
)
```

All workers see the new goal on their next spawn (via AGENTS.md).

To restart a completed project:

```
agora_update_project(
    name="my-project",
    goal="Add multi-tenant support",
    reactivate=True,
)
```

## How Worker Assignment Works

- Tasks are assigned by **role name** (e.g. `assignee="developer"`), not by
  worker name. The system routes to the correct worker automatically.
- The leader decides which role to assign based on the discussion outcome.
- Workers see the team roster (name → role) in AGENTS.md.

## Discussion Templates

For common decision types, use a template:

```
agora_raise_motion(
    title="Should we use SQLite or PostgreSQL?",
    template="tech_choice",
)
```

| Template | Participants | Use When |
|----------|-------------|----------|
| `tech_choice` | architect, developer, reviewer | Choosing between technologies |
| `bug_analysis` | developer, tester, reviewer | Root cause analysis |
| `architecture_review` | architect, developer, reviewer | Design review |
| `security_audit` | reviewer, developer, architect | Security assessment |

## Common Patterns

### Minimal Team (3 members)
- `leader` — manages project, chairs discussions
- `developer` — writes code
- `tester` — tests and validates

### Full Development Team (6 members)
- `leader` — management
- `architect` — design
- `developer` — implementation
- `reviewer` — code review
- `tester` — QA
- `researcher` — research and analysis

### Content Creation Team (4 members)
- `leader` — editorial direction
- `researcher` — fact-finding
- `writer` — content production
- `reviewer` — editorial review

## Troubleshooting

- **Nothing happens after starting?** Normal for up to one heartbeat interval
  (default 15 min) — the leader is only woken at heartbeat time. Check
  `hermes cron list` for `heartbeat-<project_name>`, or Trigger manually.
- **Workers discuss and plan but never write files or run commands?** The
  project was started without `allow_unattended`, so their flagged actions fail
  closed. Re-run `agora_start_project` for that name with
  `allow_unattended=True` (or use the Dashboard's project settings).
- **Workers not picking up tasks?** Check that the gateway is running and
  the kanban dispatcher is active: `hermes gateway status`
- **Discussions not starting?** Check that the leader has `agora` toolset
  (it should be automatic). Check `agora_list_motions(status="active")`.
- **Heartbeat not firing?** Check cron: `hermes cron list`. Look for
  `heartbeat-<project_name>`.
- **No Agora tab in the dashboard?** You restarted only the gateway —
  the dashboard discovers plugin tabs at startup: `hermes dashboard restart`.
- **Worker says "No inference provider configured"?** The global model config
  is missing, or its `.env` never reached the profile. Check `~/.hermes/.env`
  and `~/.hermes/profiles/<name>/.env`.
- **Worker crashing repeatedly with 429/503?** API rate limiting. Set
  `api_max_retries` (e.g. 50) on the worker profile, or globally
  `hermes config set agent.api_max_retries 50`.
- **Resetting a worker?** Workers have no `memory` tool (removed in v1.8.7+).
  To reset one, use `agora_remove_worker` + `agora_create_worker`, or edit its
  SOUL.md at `~/.hermes/profiles/<name>/SOUL.md`.

## Code Review Workflow (v1.8.6+)

When a team includes a reviewer role, developers can submit tasks for code
review instead of marking them done directly:

```
agora_close_task(task_id="t_xxx", action="submit_review")
```

This transitions the task to `review` status and auto-assigns to the reviewer
worker. The dispatcher auto-spawns the reviewer. After review, the task goes
to `done`. The leader does NOT need to create separate review tasks.
