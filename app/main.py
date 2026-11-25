from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from typing import Optional
import asyncio
import os
import re
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

from app.database import init_db, set_system_setting
from app.routers import properties, pipeline, calculations, export, settings, alerts, auth, portal_health
from app.scheduler import start_background_scheduler, stop_background_scheduler
from app import mcp_server
from app.auth import get_current_user

# Initialize Database and seed data on startup
init_db()

app = FastAPI(
    title="ImmoMetrica Clean-Room Clone API",
    description="High-performance Real Estate Meta-Search, Dealboard, Valuation & Investment Analysis Engine",
    version="2.0.0"
)

_CORS_ORIGINS = [
    "https://immofinder-poggel-micciche.duckdns.org",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
for _env_origin in [os.getenv("APP_BASE_URL", ""), os.getenv("CORS_ORIGINS", "")]:
    for _item in _env_origin.split(","):
        _cleaned = _item.strip().rstrip("/")
        if _cleaned and _cleaned not in _CORS_ORIGINS:
            _CORS_ORIGINS.append(_cleaned)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

_cached_server_url: Optional[str] = None
_cached_local_url: Optional[str] = None

# Authentication Middleware for API and private static HTML templates
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    global _cached_server_url, _cached_local_url
    path = request.url.path

    # Auto-detect incoming public base URL from browser traffic (only persist when changed)
    try:
        if path in ("/", "/login") or path.startswith("/deal/") or path.startswith("/property/"):
            host_hdr = request.headers.get("x-forwarded-host") or request.headers.get("host")
            proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
            if host_hdr:
                cleaned_host = host_hdr.split(",")[0].strip()
                detected_url = f"{proto}://{cleaned_host}".rstrip("/")
                is_local = any(cleaned_host.startswith(p) for p in ["127.0.0.1", "localhost", "0.0.0.0", "192.168.", "10."])
                is_raw_ip = bool(re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?$", cleaned_host))
                if not is_local and not is_raw_ip:
                    if detected_url != _cached_server_url:
                        _cached_server_url = detected_url
                        await asyncio.to_thread(set_system_setting, "last_server_base_url", detected_url)
                elif is_local:
                    if detected_url != _cached_local_url:
                        _cached_local_url = detected_url
                        await asyncio.to_thread(set_system_setting, "last_local_base_url", detected_url)
    except Exception:
        pass

    # Prevent unauthenticated access to assembled fallback index/layout/partials via /static
    if path in ("/static/index.html", "/static/layout.html") or path.startswith("/static/partials"):
        if not get_current_user(request):
            return JSONResponse(
                status_code=401,
                content={"detail": "Nicht autorisiert. Bitte anmelden.", "error": "unauthorized"}
            )

    # /mcp/call and /mcp/tools verify Bearer MCP token (or web session) directly in mcp_server
    if path in ("/mcp/call", "/mcp/tools"):
        return await call_next(request)

    # Public endpoints that don't require session authentication
    if (
        path in ("/login", "/favicon.ico", "/favicon.svg", "/apple-touch-icon.png", "/docs", "/openapi.json", "/ping", "/health", "/api/health")
        or path.startswith("/static")
        or path.startswith("/api/auth")
        or (not path.startswith("/api/") and not path.startswith("/mcp"))
    ):
        return await call_next(request)

    # Protected API and /mcp/* endpoints (/mcp/status, /mcp/snippet, /mcp/regenerate-token, /api/*)
    user = get_current_user(request)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"detail": "Nicht autorisiert. Bitte anmelden.", "error": "unauthorized"}
        )

    return await call_next(request)

# Register routers
app.include_router(auth.router)
app.include_router(properties.router)
app.include_router(pipeline.router)
app.include_router(calculations.router)
app.include_router(export.router)
app.include_router(settings.router)
app.include_router(alerts.router)
app.include_router(portal_health.router)
app.include_router(mcp_server.router)

@app.get("/api/health")
@app.get("/health")
def health_check():
    return {"status": "ok", "app": "immometrica", "version": "2.0.0"}

@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    ico_path = os.path.join(static_dir, "favicon.ico")
    if os.path.exists(ico_path):
        return FileResponse(ico_path, media_type="image/x-icon")
    svg_path = os.path.join(static_dir, "favicon.svg")
    return FileResponse(svg_path, media_type="image/svg+xml")

@app.get("/favicon.svg", include_in_schema=False)
def favicon_svg():
    svg_path = os.path.join(static_dir, "favicon.svg")
    return FileResponse(svg_path, media_type="image/svg+xml")

@app.get("/apple-touch-icon.png", include_in_schema=False)
def apple_touch_icon():
    p = os.path.join(static_dir, "apple-touch-icon.png")
    return FileResponse(p, media_type="image/png")

@app.on_event("startup")
async def on_startup():
    start_background_scheduler()

@app.on_event("shutdown")
async def on_shutdown():
    stop_background_scheduler()

# Mount static files
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

def assemble_index_html() -> str:
    layout_file = os.path.join(static_dir, "layout.html")
    partials_dir = os.path.join(static_dir, "partials")
    if not os.path.exists(layout_file):
        index_file = os.path.join(static_dir, "index.html")
        if os.path.exists(index_file):
            with open(index_file, "r", encoding="utf-8") as f:
                return f.read()
        return "<html><body>ImmoMetrica Clone API is running</body></html>"

    with open(layout_file, "r", encoding="utf-8") as f:
        html = f.read()

    replacements = {
        "<!-- {{HEADER}} -->": "header.html",
        "<!-- {{TAB_FEED}} -->": "tab_feed.html",
        "<!-- {{TAB_BOARD}} -->": "tab_board.html",
        "<!-- {{TAB_CALCULATOR}} -->": "tab_calculator.html",
        "<!-- {{TAB_MAP}} -->": "tab_map.html",
        "<!-- {{TAB_INFO}} -->": "tab_info.html",
        "<!-- {{TAB_ALERTS}} -->": "tab_alerts.html",
        "<!-- {{MODALS}} -->": "modals.html",
    }

    for placeholder, filename in replacements.items():
        partial_path = os.path.join(partials_dir, filename)
        if os.path.exists(partial_path):
            with open(partial_path, "r", encoding="utf-8") as pf:
                partial_content = pf.read()
            html = html.replace(placeholder, partial_content)

    return html

def serve_login_page() -> HTMLResponse:
    login_file = os.path.join(static_dir, "login.html")
    if os.path.exists(login_file):
        with open(login_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    return HTMLResponse(content="<h1>Anmeldung erforderlich</h1>", status_code=200)

@app.get("/login")
def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return HTMLResponse(
            content="<script>window.location.href = '/';</script>",
            status_code=200
        )
    return serve_login_page()

@app.get("/")
@app.get("/deal/{deal_id}")
@app.get("/property/{deal_id}")
def serve_index(request: Request, deal_id: Optional[int] = None):
    user = get_current_user(request)
    if not user:
        return serve_login_page()

    content = assemble_index_html()
    return HTMLResponse(
        content=content,
        status_code=200,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )
