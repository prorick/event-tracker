"""event-tracker — your todo-mcp tools and your skills, behind one connector.

Same shape as your-protected-notes (guest commons vs. private shelf, decided
by one Authorization header), applied to the todo-mcp task list so the
inbox-radar skill can run from claude.ai — where the Gmail connector lives —
and file into a list that is YOURS.

  guest — no token (or the declared guest credential): a shared commons.
  user  — a verified Neon Auth JWT: your private task list, keyed by `sub`.
  error — an invalid/expired token fails loudly; never silently guest.

Skills: every skills/<name>/SKILL.md is served three ways, because clients
differ in what they surface — as an MCP prompt, as an MCP resource
(skill://<name>), and through the list_skills / get_skill tools (tools are
the one thing every connector client reliably exposes).
"""
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from mcp.server.fastmcp import Context, FastMCP

DATABASE_URL = os.environ.get("DATABASE_URL")
NEON_AUTH_BASE_URL = os.environ.get("NEON_AUTH_BASE_URL", "").rstrip("/")
JWKS_URL = os.environ.get("NEON_AUTH_JWKS_URL") or (
    NEON_AUTH_BASE_URL + "/.well-known/jwks.json" if NEON_AUTH_BASE_URL else None)
# The server runs in UTC on Render, but due times are typed by a human in
# their own timezone. A naive due ('2026-10-21T17:00') is read in THIS zone.
TZ = ZoneInfo(os.environ.get("TASKS_TZ", "America/New_York"))
SKILLS_DIR = Path(__file__).parent / "skills"

# Crockford Base32 (no I, L, O, U — unambiguous aloud). 32^4 ids per list.
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
GUEST_TOKEN = "guest-no-shelf"  # must match auth.GUEST_TOKEN

mcp = FastMCP(
    "event-tracker",
    instructions=(
        "A personal task list plus skills (playbooks). When a request matches a skill in "
        "list_skills — e.g. 'what's on today', 'did I miss anything' -> inbox-radar — call "
        "get_skill and follow it, using this connector's task tools and the Gmail connector."),
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)

# Ids are unique PER LIST, not globally; NULL user_id = the guest commons.
SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id         CHAR(4) NOT NULL,
    user_id    TEXT,
    title      TEXT NOT NULL,
    category   TEXT NOT NULL DEFAULT '',
    due        TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'manual',
    difficulty INT  NOT NULL DEFAULT 0,
    hours      REAL NOT NULL DEFAULT 0,
    done       BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS tasks_list_id ON tasks (id, COALESCE(user_id, ''));
"""
COLS = "id, title, category, due, notes, source, difficulty, hours, done"
_schema_ready = False


def db():
    global _schema_ready
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set — see README setup (Neon -> Connect).")
    conn = psycopg.connect(DATABASE_URL)
    if not _schema_ready:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
        conn.commit()
        _schema_ready = True
    return conn


def new_id():
    return "".join(secrets.choice(CROCKFORD) for _ in range(4))


# ---------------------------------------------------------------- identity
def current_identity(ctx: Context):
    """Resolve the caller: (user_id, email), or (None, None) for a guest.

    ABSENT header  -> guest (a mode, not an error).
    ANY other header that isn't a valid credential -> raises. A broken
    credential must never silently degrade to the shared commons, or your
    tasks would quietly land where every guest can read them.
    """
    request = getattr(ctx.request_context, "request", None)  # None under stdio
    auth = request.headers.get("authorization", "") if request is not None else ""
    if not auth:
        return None, None
    if not auth.lower().startswith("bearer "):
        raise PermissionError("Authorization header is not a Bearer token.")
    token = auth.split(None, 1)[1]
    if token == GUEST_TOKEN:
        return None, None
    import jwt as pyjwt
    from jwt import PyJWKClient
    if not JWKS_URL:
        raise RuntimeError("NEON_AUTH_BASE_URL is not set — see README setup (Neon -> Auth).")
    signing_key = PyJWKClient(JWKS_URL).get_signing_key_from_jwt(token)
    claims = pyjwt.decode(token, signing_key.key, algorithms=["EdDSA", "ES256", "RS256"],
                          options={"verify_aud": False})
    return claims["sub"], claims.get("email")


def _shelf(user_id):
    return "your list" if user_id else "the guest commons"


# ------------------------------------------------------------------ format
def _parse_due(due: str) -> datetime | None:
    if not due:
        return None
    d = datetime.fromisoformat(due)
    return d.replace(tzinfo=TZ) if d.tzinfo is None else d


def _format(t: dict, now: datetime) -> str:
    d = _parse_due(t["due"])
    overdue = d is not None and d < now and not t["done"]
    marker = "[x]" if t["done"] else ("[!]" if overdue else "[ ]")
    cat = f"{t['category']}: " if t["category"] else ""
    due_str = f" — due {t['due']}" + (" (OVERDUE)" if overdue else "") if t["due"] else ""
    extras = []
    if t["difficulty"]:
        extras.append(f"difficulty {t['difficulty']}/5")
    if t["hours"]:
        extras.append(f"~{t['hours']:g}h")
    extra_str = f" ({', '.join(extras)})" if extras else ""
    notes = f"\n      {t['notes']}" if t["notes"] else ""
    return f"{marker} {t['id']} {cat}{t['title']}{due_str}{extra_str}{notes}"


def _row(r) -> dict:
    return dict(zip([c.strip() for c in COLS.split(",")], r))


def _validate_due(due: str) -> str | None:
    if not due:
        return None
    try:
        _parse_due(due)
    except ValueError:
        return f"due must be ISO ('2026-10-21' or '2026-10-21T13:30'), got {due!r}."
    return None


# ------------------------------------------------------------------- tools
@mcp.tool()
def user_status(ctx: Context) -> str:
    """Who does this server think you are? Always available, in every mode."""
    user_id, email = current_identity(ctx)
    if user_id is None:
        return "guest — tasks go to the shared guest commons. Sign in to get a private list."
    return f"user — signed in as {email or user_id}. Your tasks are yours alone."


@mcp.tool()
def add_task(
    ctx: Context,
    title: str,
    category: str = "",
    due: str = "",
    notes: str = "",
    source: str = "manual",
    difficulty: int = 0,
    hours: float = 0,
) -> str:
    """Add a task. due is an ISO date/datetime ('2026-09-22' or '2026-09-22T13:30'), read in the
    user's local timezone; leave blank if unknown. difficulty is 1 (easy) to 5 (hard), 0 if unset.
    hours is an estimated time-to-complete, 0 if unset."""
    if err := _validate_due(due):
        return err
    user_id, _ = current_identity(ctx)
    with db() as conn, conn.cursor() as cur:
        for _ in range(5):
            try:
                cur.execute(
                    f"INSERT INTO tasks (user_id, {COLS}) VALUES "
                    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, false) RETURNING " + COLS,
                    (user_id, new_id(), title, category, due, notes, source, difficulty, hours))
                t = _row(cur.fetchone())
                conn.commit()
                return f"Added to {_shelf(user_id)}: " + _format(t, datetime.now(TZ))
            except psycopg.errors.UniqueViolation:
                conn.rollback()
    return "Could not find a free task id after 5 tries — the list may be enormous."


@mcp.tool()
def list_tasks(ctx: Context, scope: str = "all", include_done: bool = False) -> str:
    """List tasks. scope: 'today', 'week' (next 7 days + overdue), 'urgent' (overdue or due within 48h), or 'all'."""
    user_id, _ = current_identity(ctx)
    with db() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {COLS} FROM tasks WHERE user_id IS NOT DISTINCT FROM %s"
                    + ("" if include_done else " AND NOT done"), (user_id,))
        tasks = [_row(r) for r in cur.fetchall()]
    now = datetime.now(TZ)

    def matches(t: dict) -> bool:
        if scope == "all":
            return True
        d = _parse_due(t["due"])
        if d is None:
            return False
        if scope == "today":
            return d.astimezone(TZ).date() == now.date()
        if scope == "week":
            return d <= now + timedelta(days=7)
        if scope == "urgent":
            return d <= now + timedelta(hours=48)
        return True

    filtered = [t for t in tasks if matches(t)]
    filtered.sort(key=lambda t: (_parse_due(t["due"]) is None, _parse_due(t["due"]) or now,
                                 -t["difficulty"], -t["hours"]))
    if not filtered:
        hint = ""
        if "inbox-radar" in SKILLS:
            # An empty list usually means "not scanned yet", not "nothing happening".
            hint = (" If the user is asking about their events, this list may just not be filled "
                    "yet: call get_skill('inbox-radar') and follow it to scan Gmail and file them.")
        return f"Nothing in '{scope}' on {_shelf(user_id)}.{hint}"
    header = f"{_shelf(user_id).capitalize()} — {now:%a %Y-%m-%d %H:%M} ({TZ.key}):\n"
    return header + "\n".join(_format(t, now) for t in filtered)


@mcp.tool()
def complete_task(ctx: Context, task_id: str) -> str:
    """Mark a task done by its id."""
    user_id, _ = current_identity(ctx)
    with db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE tasks SET done = true WHERE id = %s AND user_id IS NOT DISTINCT FROM %s "
                    "RETURNING title", (task_id.upper(), user_id))
        row = cur.fetchone()
        conn.commit()
    return f"Completed: {row[0]}" if row else f"No task with id {task_id} on {_shelf(user_id)}"


@mcp.tool()
def delete_task(ctx: Context, task_id: str) -> str:
    """Delete a task by its id."""
    user_id, _ = current_identity(ctx)
    with db() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM tasks WHERE id = %s AND user_id IS NOT DISTINCT FROM %s",
                    (task_id.upper(), user_id))
        n = cur.rowcount
        conn.commit()
    return f"Deleted task {task_id}" if n else f"No task with id {task_id} on {_shelf(user_id)}"


@mcp.tool()
def update_task(
    ctx: Context,
    task_id: str,
    title: str = "",
    category: str = "",
    due: str = "",
    notes: str = "",
    difficulty: int = 0,
    hours: float = 0,
) -> str:
    """Update fields of an existing task. Leave a field at its default (blank/0) to keep its current value."""
    if err := _validate_due(due):
        return err
    user_id, _ = current_identity(ctx)
    changes = {k: v for k, v in dict(title=title, category=category, due=due, notes=notes,
                                     difficulty=difficulty, hours=hours).items() if v}
    with db() as conn, conn.cursor() as cur:
        sets = ", ".join(f"{k} = %s" for k in changes) or "id = id"
        cur.execute(f"UPDATE tasks SET {sets} WHERE id = %s AND user_id IS NOT DISTINCT FROM %s "
                    "RETURNING " + COLS, (*changes.values(), task_id.upper(), user_id))
        row = cur.fetchone()
        conn.commit()
    if not row:
        return f"No task with id {task_id} on {_shelf(user_id)}"
    return "Updated " + _format(_row(row), datetime.now(TZ))


# ------------------------------------------------------------------ skills
def _load_skills() -> dict[str, dict]:
    """skills/<name>/SKILL.md -> {name: {description, body}} (frontmatter split off)."""
    skills = {}
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = path.read_text()
        meta, body = {}, text
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
        if m:
            body = m.group(2)
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
        name = meta.get("name", path.parent.name)
        skills[name] = {"description": meta.get("description", ""), "body": body.strip()}
    return skills


SKILLS = _load_skills()


def _skill_text(name: str) -> str:
    now = datetime.now(TZ)
    return (f"# Skill: {name}\n_Now: {now:%A %Y-%m-%d %H:%M} ({TZ.key}). The todo tools named "
            f"below (add_task, list_tasks, …) are this connector's tools._\n\n{SKILLS[name]['body']}")


@mcp.tool()
def list_skills() -> str:
    """List the skills (playbooks) this connector carries. When the user's request matches a
    skill's description, call get_skill with its name and follow those instructions."""
    if not SKILLS:
        return "No skills installed."
    return "\n".join(f"- {n}: {s['description']}" for n, s in SKILLS.items())


@mcp.tool()
def get_skill(name: str) -> str:
    """Fetch a skill's full instructions by name (see list_skills), then follow them."""
    if name not in SKILLS:
        return f"No skill named {name!r}. Available: {', '.join(SKILLS) or 'none'}."
    return _skill_text(name)


def _register_skill(name: str, description: str):
    # Each skill also becomes a prompt and a resource, for clients that surface those.
    mcp.prompt(name=name, description=description)(lambda: _skill_text(name))
    mcp.resource(f"skill://{name}", name=name, description=description,
                 mime_type="text/markdown")(lambda: _skill_text(name))


for _name, _skill in SKILLS.items():
    _register_skill(_name, _skill["description"])


from auth import register_oauth_routes  # noqa: E402  OAuth AS endpoints for claude.ai

register_oauth_routes(mcp)

if __name__ == "__main__":
    import sys
    mcp.run(transport="stdio" if "--stdio" in sys.argv else "streamable-http")
