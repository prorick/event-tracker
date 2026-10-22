# event-tracker

**Your todo-mcp tools and your skills, behind one claude.ai connector, on a
list that is yours.** Same structure as
[your-protected-notes](https://github.com/prorick/your-protected-notes):
one table, one Authorization header, three modes.

| mode | trigger | what you see |
|---|---|---|
| **guest** | no `Authorization` header (or "Continue as guest") | the shared guest commons |
| **user** | valid Neon Auth JWT (Managed Better Auth) | your private task list |
| **error** | invalid/expired/non-Bearer credential | a loud failure, never a silent fallback to guest |

**Why it exists.** `inbox-radar` needs Gmail and a task list together.
claude.ai already has the Gmail connector, but your `todo-mcp` server only ran
over stdio on your laptop. This server puts those same task tools online, so
claude.ai can run inbox-radar from any device.

## What the connector exposes

- **Task tools**: `add_task` · `list_tasks` · `update_task` · `complete_task`
  · `delete_task`. They have the same names and arguments as `todo-mcp`, so
  `skills/inbox-radar/SKILL.md` works unchanged. Ids are 4-character
  Crockford Base32 codes, such as `X642`. Naive due times are read in
  `TASKS_TZ`, which defaults to America/New_York, not in the server's UTC.
- **`user_status`**: reports who the server thinks you are.
- **Skills**: each `skills/<name>/SKILL.md` is served in three ways:
  - the `list_skills` and `get_skill` tools, which every client exposes;
  - an MCP **prompt** named after the skill;
  - a `skill://<name>` **resource**.

  The server's `instructions` tell Claude to check `list_skills` when a
  request matches. Asking "what's on today?" makes Claude fetch inbox-radar
  and follow it. To add a skill, drop a folder into `skills/` and redeploy.

## Setup

1. **Neon**: create a project. Under **Auth**, enable it, which provisions
   Managed Better Auth. Copy:
   - the connection string from Connect, as `DATABASE_URL`;
   - the Auth URL from Configuration, as `NEON_AUTH_BASE_URL`.
2. **Render**: go to New → Blueprint and pick this repo. Fill in
   `DATABASE_URL`, `NEON_AUTH_BASE_URL`, and `PUBLIC_URL`. `PUBLIC_URL` is
   the service URL Render gives you; if you only learn it after the first
   deploy, set it and redeploy.
3. **claude.ai**: go to Settings → Connectors → Add custom connector and
   enter `https://<service>.onrender.com/mcp`.
   - Authentication: **Sign in now**.
   - OAuth client: **Register automatically (DCR)**.
   - On this server's login page, choose **sign up** or **sign in**. Then
     ask "what's my user status?"
4. **Optional: bring your old tasks** into your private list:
   `uv run --env-file .env python scripts/import_tasks_json.py <path/to/tasks.json> <your-email>`
   (add `--dry-run` first to preview).
5. **Try it**: with the Gmail and event-tracker connectors both on, ask
   "what's on today?"

**Local run**: copy `.env.example` to `.env`, then run
`uv run --env-file .env server.py`. Smoke test it with
`uv run python scripts/smoke_test.py http://127.0.0.1:8000`, which also works
against your Render URL.

## Known limits (prototype-grade, like the original)

- OAuth clients and pending codes are kept in memory. After a Render restart,
  claude.ai re-registers, so you may need to reconnect once.
- Render's free tier sleeps when idle. The first call after a nap takes about
  30–60 seconds.
- Access tokens last about 15 minutes. They refresh through the Neon Auth
  session.

*Adapted from prorick/your-protected-notes (MIT): `auth.py`'s OAuth flow and
the three-mode identity rule. CIS 7000, Penn, Fall 2026.*
