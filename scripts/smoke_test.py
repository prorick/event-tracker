"""Smoke test a running event-tracker: guest mode, the three-mode rule, skills, OAuth guest dance.

    uv run python scripts/smoke_test.py http://127.0.0.1:8000
    uv run python scripts/smoke_test.py https://<service>.onrender.com

Writes then deletes one task in the GUEST commons; never touches a private list.
"""
import asyncio
import base64
import hashlib
import json
import secrets
import sys
import urllib.parse
import urllib.request

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")


def text(result):
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


async def session_call(headers, fn):
    async with streamablehttp_client(BASE + "/mcp", headers=headers) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)


async def main():
    async def guest_flow(s):
        tools = sorted(t.name for t in (await s.list_tools()).tools)
        print("tools:", tools)
        print("status:", text(await s.call_tool("user_status", {})))
        added = text(await s.call_tool("add_task", {
            "title": "smoke test event", "category": "optional", "due": "2026-10-15T23:59",
            "notes": "Desc: test | Where: ? | Food: no | Other: smoke", "source": "gmail"}))
        print(added)
        tid = added.split("] ")[1].split()[0]
        print(text(await s.call_tool("update_task", {"task_id": tid, "due": "2026-10-16T09:00"})))
        print(text(await s.call_tool("list_tasks", {"scope": "all"})))
        print(text(await s.call_tool("complete_task", {"task_id": tid})))
        print(text(await s.call_tool("delete_task", {"task_id": tid})))
        print(text(await s.call_tool("add_task", {"title": "bad", "due": "next tuesday"})))
        print("skills:", text(await s.call_tool("list_skills", {})))
        print("get_skill:", text(await s.call_tool("get_skill", {"name": "inbox-radar"}))[:160], "…")
        print("prompts:", [p.name for p in (await s.list_prompts()).prompts])
        print("resources:", [str(r.uri) for r in (await s.list_resources()).resources])

    await session_call({}, guest_flow)

    for label, hdr in [("invalid JWT", "Bearer not.a.jwt"), ("non-bearer", "Basic abc")]:
        res = await session_call({"Authorization": hdr},
                                 lambda s: s.call_tool("user_status", {}))
        assert res.isError, f"{label} should fail loudly"
        print(f"{label}: fails loudly ✓")

    # OAuth: register -> authorize (guest) -> token, with PKCE.
    def post(path, data, form=True):
        body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
        req = urllib.request.Request(BASE + path, body, {"Content-Type":
              "application/x-www-form-urlencoded" if form else "application/json"})
        return urllib.request.urlopen(req)

    redirect = "http://127.0.0.1:9/callback"
    client = json.load(post("/register", {"redirect_uris": [redirect]}, form=False))
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    form = {"client_id": client["client_id"], "redirect_uri": redirect, "state": "xyz",
            "code_challenge": challenge, "code_challenge_method": "S256", "mode": "guest"}
    try:
        opener.open(urllib.request.Request(BASE + "/authorize", urllib.parse.urlencode(form).encode()))
    except urllib.error.HTTPError as e:
        loc = e.headers["Location"]
    code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]
    tok = json.load(post("/token", {"grant_type": "authorization_code", "code": code,
                                    "code_verifier": verifier, "redirect_uri": redirect}))
    print("oauth guest dance → access_token:", tok["access_token"])
    bad = dict(form, redirect_uri="https://evil.example/cb")
    try:
        opener.open(urllib.request.Request(BASE + "/authorize", urllib.parse.urlencode(bad).encode()))
        print("unregistered redirect_uri: NOT rejected ✗")
    except urllib.error.HTTPError as e:
        print("unregistered redirect_uri rejected:", e.code, "✓")


asyncio.run(main())
