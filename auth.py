"""Google OAuth 2.0 helpers for JLPT 單字王."""
import os
import secrets
from urllib.parse import urlencode

import httpx

GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")

def _default_redirect_uri() -> str:
    production = os.getenv("PRODUCTION", "false").lower() == "true"
    port = os.getenv("PORT", "8000")
    if production:
        domain = os.getenv("DOMAIN", "vividuck.com")
        return f"https://{domain}/auth/google/callback"
    return f"http://localhost:{port}/auth/google/callback"

GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", _default_redirect_uri())

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

_pending_states: set[str] = set()


def build_auth_url() -> str:
    """Generate Google OAuth authorization URL and register its state."""
    state = secrets.token_urlsafe(16)
    _pending_states.add(state)
    params = urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })
    return f"{_AUTH_URL}?{params}"


def validate_state(state: str) -> bool:
    """Validate and consume a one-time OAuth state (CSRF protection)."""
    if state in _pending_states:
        _pending_states.discard(state)
        return True
    return False


async def fetch_google_user(code: str) -> dict:
    """Exchange authorization code for tokens, then fetch user profile."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        token_resp = await client.post(_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        user_resp = await client.get(_USERINFO_URL, headers={
            "Authorization": f"Bearer {access_token}"
        })
        user_resp.raise_for_status()
        # Returns: {sub, name, email, picture, ...}
        return user_resp.json()
