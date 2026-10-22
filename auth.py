"""auth.py — a minimal OAuth 2.1 authorization server for event-tracker.

Adapted from prorick/your-protected-notes (MIT). claude.ai custom connectors
obtain tokens via OAuth: discovery, dynamic client registration, PKCE, a
browser sign-in. This server IS the authorization server: its login page
checks credentials against Neon Auth (Managed Better Auth) and the token
endpoint hands back Neon Auth's own JWTs, which server.py already verifies.

Prototype-grade: in-memory stores (single instance — a Render restart forgets
registered clients and pending codes; claude.ai just re-registers), public
clients + PKCE only, codes expire in 5 minutes.
"""
import base64
import hashlib
import html
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

ISSUER = os.environ.get("PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
NEON_AUTH_BASE_URL = os.environ.get("NEON_AUTH_BASE_URL", "").rstrip("/")
BA_COOKIE = "__Secure-neon-auth.session_token"
GUEST_TOKEN = "guest-no-shelf"  # declared guest credential; server.py maps it to guest mode
PARAMS = ("client_id", "redirect_uri", "state", "code_challenge",
          "code_challenge_method", "response_type", "scope", "resource")

_codes: dict = {}    # code -> {tokens, code_challenge, redirect_uri, client_id, exp}
_clients: dict = {}  # client_id -> {redirect_uris}


def _better(path, payload=None, cookie=None, method=None):
    """Managed Better Auth REST. Origin header is required; the session
    travels as a cookie; GET /token exchanges session -> EdDSA JWT (~15 min)."""
    origin = NEON_AUTH_BASE_URL.split("/neondb")[0] if "/neondb" in NEON_AUTH_BASE_URL else NEON_AUTH_BASE_URL
    h = {"Content-Type": "application/json", "Origin": origin}
    if cookie:
        h["Cookie"] = f"{BA_COOKIE}={cookie}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(NEON_AUTH_BASE_URL + path, data, h, method=method)
    with urllib.request.urlopen(req) as r:
        session = ""
        for c in r.headers.get_all("Set-Cookie") or []:
            first = c.split(";")[0]
            if first.startswith(BA_COOKIE + "="):
                session = first.split("=", 1)[1]
        return json.loads(r.read() or b"{}"), session


def _better_login(email, password, signup):
    """Returns (jwt_access_token, session) — the session doubles as refresh token."""
    if not NEON_AUTH_BASE_URL:
        raise RuntimeError("NEON_AUTH_BASE_URL is not set")
    payload = {"email": email, "password": password}
    if signup:
        payload["name"] = email.split("@")[0]
    _, session = _better("/sign-up/email" if signup else "/sign-in/email", payload)
    if not session:
        raise RuntimeError("no session cookie returned")
    out, _ = _better("/token", cookie=session, method="GET")
    return out["token"], session


LOGIN_FORM = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in — event-tracker</title>
<style>
body{{font-family:system-ui,sans-serif;background:#F4F6FA;display:flex;justify-content:center;padding:8vh 16px 0}}
.card{{background:#fff;border:2px solid #1F3A68;border-radius:14px;padding:28px 32px;width:340px;max-width:100%}}
h1{{color:#1F3A68;font-size:1.15em;margin:0 0 4px}}
p{{font-size:.85em;color:#555;margin:4px 0 14px}}
input,button{{width:100%;box-sizing:border-box;padding:10px;margin:5px 0;border-radius:8px;font-size:.95em}}
input{{border:1px solid #1F3A6855}}
button{{border:none;background:#1F3A68;color:#fff;font-weight:bold;cursor:pointer}}
.alt{{background:#fff;color:#1F3A68;border:2px solid #1F3A68}}
.err{{color:#C0392B;font-size:.85em}}</style></head><body>
<div class="card"><h1>event-tracker</h1>
<p>Sign in so your tasks and events stay private. {err}</p>
<form method="post" action="{issuer}/authorize">
{hidden}
<input name="email" type="email" placeholder="email" required>
<input name="password" type="password" placeholder="password" required>
<button name="mode" value="signin" type="submit">Sign in</button>
<button name="mode" value="signup" type="submit" class="alt">New here — sign up</button>
<button name="mode" value="guest" type="submit" class="alt" formnovalidate style="border-style:dashed;color:#777;border-color:#999">Continue as guest — shared commons</button>
</form></div></body></html>"""


def _redirect_ok(client_id, redirect_uri):
    """Known client -> redirect_uri must be one it registered. Unknown client
    (e.g. registered before a restart) -> allowed; PKCE still guards the code."""
    if not redirect_uri.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        return False
    client = _clients.get(client_id)
    return client is None or redirect_uri in client["redirect_uris"]


def _issue_code(params, tokens):
    code = secrets.token_urlsafe(24)
    _codes[code] = {"tokens": tokens, "code_challenge": params["code_challenge"],
                    "redirect_uri": params["redirect_uri"], "client_id": params["client_id"],
                    "exp": time.time() + 300}
    sep = "&" if "?" in params["redirect_uri"] else "?"
    state = urllib.parse.quote(params["state"], safe="")
    return RedirectResponse(f'{params["redirect_uri"]}{sep}code={code}&state={state}', status_code=302)


def register_oauth_routes(mcp):
    @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
    async def as_metadata(request: Request):
        return JSONResponse({
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "registration_endpoint": f"{ISSUER}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        })

    @mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
    async def pr_metadata(request: Request):
        return JSONResponse({"resource": f"{ISSUER}/mcp", "authorization_servers": [ISSUER]})

    @mcp.custom_route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
    async def pr_metadata_suffixed(request: Request):  # RFC 9728 path-suffixed variant
        return await pr_metadata(request)

    @mcp.custom_route("/register", methods=["POST"])
    async def register(request: Request):
        body = json.loads(await request.body() or b"{}")
        client_id = secrets.token_urlsafe(16)
        uris = body.get("redirect_uris", [])
        _clients[client_id] = {"redirect_uris": uris}
        return JSONResponse({"client_id": client_id, "token_endpoint_auth_method": "none",
                             "redirect_uris": uris,
                             "grant_types": ["authorization_code", "refresh_token"],
                             "response_types": ["code"]}, status_code=201)

    def _render_form(params, err=""):
        hidden = "\n".join(f'<input type="hidden" name="{k}" value="{html.escape(v)}">'
                           for k, v in params.items())
        return HTMLResponse(LOGIN_FORM.format(
            issuer=ISSUER, hidden=hidden,
            err=f'<span class="err">{html.escape(err)}</span>' if err else ""))

    @mcp.custom_route("/authorize", methods=["GET"])
    async def authorize_get(request: Request):
        params = {k: request.query_params.get(k, "") for k in PARAMS}
        if not _redirect_ok(params["client_id"], params["redirect_uri"]):
            return JSONResponse({"error": "invalid_request",
                                 "error_description": "redirect_uri not registered"}, status_code=400)
        return _render_form(params)

    @mcp.custom_route("/authorize", methods=["POST"])
    async def authorize_post(request: Request):
        form = dict(await request.form())
        params = {k: form.get(k, "") for k in PARAMS}
        if not _redirect_ok(params["client_id"], params["redirect_uri"]):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        if form.get("mode") == "guest":
            return _issue_code(params, {"access_token": GUEST_TOKEN, "refresh_token": GUEST_TOKEN})
        signup = form.get("mode") == "signup"
        try:
            jwt_tok, session = _better_login(form.get("email", ""), form.get("password", ""), signup)
        except (urllib.error.HTTPError, RuntimeError) as e:
            if signup and getattr(e, "code", 0) in (409, 422):
                msg = "This email already has an account — press Sign in instead."
            elif not signup:
                msg = "Sign-in failed — wrong password, or no account yet (then use sign up)."
            else:
                msg = "Sign-up failed — password may be too weak (8+ characters), or try sign in."
            return _render_form(params, err=msg)
        return _issue_code(params, {"access_token": jwt_tok, "refresh_token": "ba:" + session})

    @mcp.custom_route("/token", methods=["POST"])
    async def token(request: Request):
        form = dict(await request.form())
        grant = form.get("grant_type")
        if grant == "authorization_code":
            rec = _codes.pop(form.get("code", ""), None)
            if not rec or rec["exp"] < time.time():
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if form.get("redirect_uri") and form["redirect_uri"] != rec["redirect_uri"]:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            verifier = form.get("code_verifier", "")
            expect = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if expect != rec["code_challenge"]:
                return JSONResponse({"error": "invalid_grant",
                                     "error_description": "PKCE verification failed"}, status_code=400)
            t = rec["tokens"]
            guest = t["access_token"] == GUEST_TOKEN
            return JSONResponse({"access_token": t["access_token"], "token_type": "bearer",
                                 "expires_in": 31536000 if guest else 900,
                                 "refresh_token": t["refresh_token"]})
        if grant == "refresh_token":
            rt = form.get("refresh_token", "")
            if rt == GUEST_TOKEN:
                return JSONResponse({"access_token": GUEST_TOKEN, "token_type": "bearer",
                                     "expires_in": 31536000, "refresh_token": GUEST_TOKEN})
            if rt.startswith("ba:"):
                try:
                    out, _ = _better("/token", cookie=rt[3:], method="GET")
                    return JSONResponse({"access_token": out["token"], "token_type": "bearer",
                                         "expires_in": 900, "refresh_token": rt})
                except Exception:
                    pass
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
