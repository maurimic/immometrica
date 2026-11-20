from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from typing import Optional
import os

from app.database import init_db
from app.routers import properties, pipeline, calculations, export, settings, alerts, auth
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Authentication Middleware for API endpoints
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Public endpoints that don't require session authentication
    if (
        path in ("/login", "/favicon.ico", "/docs", "/openapi.json", "/ping", "/health", "/api/health")
        or path.startswith("/static")
        or path.startswith("/api/auth")
        or path.startswith("/mcp")
        or not path.startswith("/api/")
    ):
        return await call_next(request)

    # Protected API endpoints
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
app.include_router(mcp_server.router)

@app.get("/api/health")
@app.get("/health")
def health_check():
    return {"status": "ok", "app": "immometrica", "version": "2.0.0"}

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
