"""Copy an existing todo-mcp tasks.json onto a signed-in user's private list.

    uv run --env-file .env python scripts/import_tasks_json.py \\
        ~/Desktop/cis7000/first-instrument-mcp/todo-mcp/tasks.json you@example.com

Sign up through the connector first (so Neon Auth knows the email). Old 8-hex
ids are replaced by 4-char Crockford ids; the old id is appended to notes.
Pass --dry-run to see what would be imported.
"""
import json
import os
import secrets
import sys

import psycopg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
args = [a for a in sys.argv[1:] if a != "--dry-run"]
dry = "--dry-run" in sys.argv
if len(args) != 2:
    sys.exit(__doc__)
path, email = args
tasks = json.load(open(os.path.expanduser(path)))

with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
    cur.execute('SELECT id FROM neon_auth."user" WHERE email = %s', (email,))
    row = cur.fetchone()
    if not row:
        sys.exit(f"No Neon Auth user with email {email} — sign up via the connector first.")
    user_id = row[0]
    import server  # same SCHEMA as the live server
    cur.execute(server.SCHEMA)
    n = 0
    for t in tasks:
        notes = (t.get("notes", "") + f" | old id {t['id']}").lstrip(" |")
        if dry:
            print(("[x] " if t.get("done") else "[ ] ") + t["title"])
            continue
        for _ in range(5):
            try:
                cur.execute(
                    "INSERT INTO tasks (id, user_id, title, category, due, notes, source, difficulty,"
                    " hours, done) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    ("".join(secrets.choice(CROCKFORD) for _ in range(4)), user_id, t["title"],
                     t.get("category", ""), t.get("due", ""), notes, t.get("source", "manual"),
                     t.get("difficulty", 0), t.get("hours", 0), t.get("done", False)))
                conn.commit()
                n += 1
                break
            except psycopg.errors.UniqueViolation:
                conn.rollback()
    print(f"{'Would import' if dry else 'Imported'} {len(tasks) if dry else n} task(s) for {email}.")
