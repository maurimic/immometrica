from fastapi import APIRouter, Request, Response, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from app.auth import (
    verify_credentials,
    get_auth_config,
    hash_session_token,
    get_current_user,
    set_session_cookie,
    clear_session_cookie,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str
    remember: Optional[bool] = True


def _is_https_request(request: Optional[Request]) -> bool:
    if not request:
        return False
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0].strip().lower()
    return proto == "https"


@router.post("/login")
def login(req: LoginRequest, request: Request, response: Response):
    """Authenticates the user and sets an HttpOnly session cookie."""
    try:
        if not verify_credentials(req.username, req.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Benutzername oder Passwort ist nicht korrekt.",
            )

        cfg = get_auth_config()
        token = hash_session_token(cfg["username"], cfg["password"], cfg["secret"])
        max_age = (30 * 86400) if req.remember else None
        set_session_cookie(response, token, max_age=max_age, secure=_is_https_request(request))

        return {
            "success": True,
            "username": cfg["username"],
            "message": "Erfolgreich angemeldet.",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Anmeldefehler: {str(e)}",
        )


@router.get("/me")
def get_me(request: Request):
    """Returns current authentication status and user information."""
    user = get_current_user(request)
    if user:
        cfg = get_auth_config()
        return {
            "authenticated": True,
            "username": user["username"],
            "auth_source": user.get("source"),
            "env_path": cfg["local_env_path"],
            "immotracker_synced": bool(cfg["immotracker_env_path"]),
        }
    return {
        "authenticated": False,
    }


@router.post("/logout")
def logout(request: Request, response: Response):
    """Logs out the user and clears the session cookie."""
    clear_session_cookie(response, secure=_is_https_request(request))
    return {"success": True, "message": "Erfolgreich abgemeldet."}
