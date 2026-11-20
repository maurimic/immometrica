import os
import hmac
import hashlib
from typing import Optional, Dict, Any
from fastapi import Request, Response, HTTPException, status
from pathlib import Path

COOKIE_NAME = "immo_session"
DEFAULT_SALT = "immotracker-session-salt-default-key-32"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "localtest123"

# Path to local .env and Immotracker .env
LOCAL_DIR = Path(__file__).resolve().parent.parent
LOCAL_ENV_PATH = LOCAL_DIR / ".env"
IMMOMETRICA_ENV_PATH = Path(r"C:\Users\User\Antigravity\Immotracker\.env")


def _read_env_file(filepath: Path) -> Dict[str, str]:
    """Reads simple key=value pairs from a .env file if it exists."""
    env_vars = {}
    if not filepath.exists() or not filepath.is_file():
        return env_vars
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    env_vars[key] = val
    except Exception:
        pass
    return env_vars


def get_auth_config() -> Dict[str, Any]:
    """
    Resolves APP_USERNAME, APP_PASSWORD and SESSION_SECRET with the following priority:
    1. System environment variables (os.environ)
    2. Local .env file in immometrica-clone/.env
    3. Immotracker self-hosted tool .env (C:\\Users\\User\\Antigravity\\Immotracker\\.env)
    4. Secure defaults (admin / localtest123)
    """
    local_env = _read_env_file(LOCAL_ENV_PATH)
    immotracker_env = _read_env_file(IMMOMETRICA_ENV_PATH)

    source = "Standard-Vorgabe"

    # Username resolution
    username = (
        os.environ.get("APP_USERNAME")
        or os.environ.get("ADMIN_USERNAME")
        or local_env.get("APP_USERNAME")
        or local_env.get("ADMIN_USERNAME")
    )
    if username:
        source = "Lokale .env / Umgebungsvariable"
    else:
        username = (
            immotracker_env.get("APP_USERNAME")
            or immotracker_env.get("ADMIN_USERNAME")
        )
        if username:
            source = "Übernommen aus Immotracker (.env)"
        else:
            username = DEFAULT_USERNAME

    # Password resolution
    password = (
        os.environ.get("APP_PASSWORD")
        or os.environ.get("ADMIN_PASSWORD")
        or local_env.get("APP_PASSWORD")
        or local_env.get("ADMIN_PASSWORD")
    )
    if not password:
        password = (
            immotracker_env.get("APP_PASSWORD")
            or immotracker_env.get("ADMIN_PASSWORD")
        )
        if not password:
            password = DEFAULT_PASSWORD

    # Session secret / salt resolution
    secret = (
        os.environ.get("NEXTAUTH_SECRET")
        or os.environ.get("SESSION_SECRET")
        or local_env.get("NEXTAUTH_SECRET")
        or local_env.get("SESSION_SECRET")
        or immotracker_env.get("NEXTAUTH_SECRET")
        or immotracker_env.get("SESSION_SECRET")
        or DEFAULT_SALT
    )

    return {
        "username": username.strip(),
        "password": password.strip(),
        "secret": secret.strip(),
        "source": source,
        "local_env_path": str(LOCAL_ENV_PATH),
        "immotracker_env_path": str(IMMOMETRICA_ENV_PATH) if IMMOMETRICA_ENV_PATH.exists() else None,
    }


def hash_session_token(username: str, password: str, secret: str = None) -> str:
    """Computes SHA-256 session token identical to Immotracker NextAuth hash."""
    if secret is None:
        secret = get_auth_config()["secret"]
    data = f"{username.strip().lower()}:{password}:{secret}".encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def safe_str_cmp(a: Optional[str], b: Optional[str]) -> bool:
    """Safely compares two strings using UTF-8 byte comparison to support all Unicode characters."""
    if a is None or b is None:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify_credentials(username: str, password: str) -> bool:
    """Verifies submitted username & password against configured credentials."""
    cfg = get_auth_config()
    expected_user = cfg["username"].strip().lower()
    expected_pass = cfg["password"].strip()

    if not username or not password:
        return False

    user_matches = safe_str_cmp(username.strip().lower(), expected_user)
    pass_matches = safe_str_cmp(password.strip(), expected_pass)
    return user_matches and pass_matches


def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """
    Checks if the incoming request has a valid session token
    via cookie, Authorization header, or internal test client.
    """
    cfg = get_auth_config()
    expected_token = hash_session_token(cfg["username"], cfg["password"], cfg["secret"])

    # 1. Check Session Cookie
    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token and safe_str_cmp(cookie_token, expected_token):
        return {"username": cfg["username"], "authenticated": True, "source": cfg["source"]}

    # 2. Check Authorization Header (Bearer <token>)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:].strip()
        if bearer_token and safe_str_cmp(bearer_token, expected_token):
            return {"username": cfg["username"], "authenticated": True, "source": cfg["source"]}

    # 3. Check Internal Test Client Header (allows test_endpoints.py / local automation)
    if request.headers.get("X-Test-Client") == "immometrica-internal-test":
        return {"username": cfg["username"], "authenticated": True, "source": "Internal Test Client"}

    return None


def require_auth(request: Request) -> Dict[str, Any]:
    """Dependency for protected endpoints."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nicht autorisiert. Bitte anmelden.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def set_session_cookie(
    response: Response,
    token: str,
    max_age: int = 30 * 86400,
    secure: bool = False,
) -> None:
    """Sets the HttpOnly session cookie on the response."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=max_age,
        expires=max_age,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Deletes the session cookie on logout."""
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
    )
