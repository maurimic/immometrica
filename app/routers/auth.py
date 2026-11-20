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


@router.post("/login")
def login(req: LoginRequest, response: Response):
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
        set_session_cookie(response, token, max_age=max_age or (7 * 86400))

        return {
            "success": True,
            "username": cfg["username"],
            "token": token,
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
    cfg = get_auth_config()
    if user:
        return {
            "authenticated": True,
            "username": user["username"],
            "auth_source": user.get("source"),
            "env_path": cfg["local_env_path"],
            "immotracker_synced": bool(cfg["immotracker_env_path"]),
        }
    return {
        "authenticated": False,
        "env_path": cfg["local_env_path"],
        "immotracker_synced": bool(cfg["immotracker_env_path"]),
    }


@router.post("/logout")
def logout(response: Response):
    """Logs out the user and clears the session cookie."""
    clear_session_cookie(response)
    return {"success": True, "message": "Erfolgreich abgemeldet."}
