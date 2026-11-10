"""Authenticated synthetic planning application and React client host."""

from collections import defaultdict, deque
from contextlib import asynccontextmanager
from threading import BoundedSemaphore
from time import monotonic
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text

from . import __version__
from .auth import require_roles
from .auth import router as auth_router
from .bootstrap import seed_demo
from .database import Database
from .fixtures import demo_scenario
from .identity import User
from .models import OptimizationResult, Scenario, ValidationReport, ValidationRequest
from .optimization import optimize
from .settings import Settings
from .validation import validate_schedule

Planner = Annotated[User, Depends(require_roles("admin", "safety_officer", "supervisor"))]


class BoundaryMiddleware:
    """Bound incoming bodies, login attempts, and browser content capabilities."""

    def __init__(self, app, body_limit=1_048_576):
        self.app = app
        self.body_limit = body_limit
        self.login_attempts = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path == "/api/v1/auth/login" and scope["method"] == "POST":
            now = monotonic()
            address = (scope.get("client") or ("unknown", 0))[0]
            recent = self.login_attempts[address]
            while recent and recent[0] <= now - 60:
                recent.popleft()
            if len(recent) >= 30:
                await JSONResponse(
                    {"detail": "Too many login attempts. Try again in a minute."},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )(scope, receive, send)
                return
            recent.append(now)
            if len(self.login_attempts) > 10_000:
                self.login_attempts = defaultdict(
                    deque,
                    {
                        key: value
                        for key, value in self.login_attempts.items()
                        if value[-1] > now - 60
                    },
                )
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > self.body_limit:
                    await JSONResponse({"detail": "Request body is too large"}, status_code=413)(
                        scope, receive, send
                    )
                    return
                if not message.get("more_body", False):
                    break
            original_receive = receive
            replayed = False

            async def replay_receive():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()

            receive = replay_receive

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"same-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                ]
                if path.startswith("/api/"):
                    headers.append((b"cache-control", b"no-store"))
                if path not in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
                    headers.append(
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; "
                            b"style-src 'self' 'unsafe-inline'; "
                            b"img-src 'self' data:; font-src 'self'; connect-src 'self'; "
                            b"object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
                        )
                    )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, secure_send)


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    owned_database = database is None
    database = database or Database(settings.database_url)
    solver_slots = BoundedSemaphore(2)

    @asynccontextmanager
    async def lifespan(_app):
        if settings.demo_mode:
            database.initialize()
            with database.session() as session:
                seed_demo(session, demo_mode=True)
        yield
        if owned_database:
            database.dispose()

    application = FastAPI(
        title="HeatShield OS — synthetic prevention workspace",
        version=__version__,
        description=(
            "Persistent, authenticated synthetic planning and intervention workflows. "
            "Policy thresholds are illustrative. Demo review and publication cannot approve "
            "real work. Use durable optimization runs for the application workflow."
        ),
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.database = database
    application.add_middleware(BoundaryMiddleware)
    application.include_router(auth_router)
    from .persistent_api import router as persistent_router

    application.include_router(persistent_router)

    @application.get("/healthz", tags=["Operations"])
    def health():
        return {"status": "ok", "version": __version__, "mode": "synthetic"}

    @application.get("/readyz", tags=["Operations"])
    def readiness():
        try:
            with database.session() as session:
                session.execute(text("SELECT 1"))
                session.execute(select(User.id).limit(1))
                session.execute(text("SELECT id FROM scenarios LIMIT 1"))
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return {"status": "ready", "mode": "synthetic"}

    @application.get("/api/v1/demo/scenario", response_model=Scenario, tags=["Planning core"])
    def demo(_user: Planner):
        return demo_scenario()

    @application.post(
        "/api/v1/scenarios/validate", response_model=ValidationReport, tags=["Planning core"]
    )
    def validate(request: ValidationRequest, _user: Planner):
        return validate_schedule(request.scenario, request.schedule)

    @application.post(
        "/api/v1/scenarios/optimize", response_model=OptimizationResult, tags=["Planning core"]
    )
    def optimization(scenario: Scenario, _user: Planner):
        if not solver_slots.acquire(blocking=False):
            raise HTTPException(503, "Planner capacity is busy. Queue a durable run or retry.")
        try:
            return optimize(scenario)
        finally:
            solver_slots.release()

    from .weather import router as weather_router

    application.include_router(weather_router)
    root = settings.frontend_dist.resolve()
    if (root / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @application.get("/", include_in_schema=False)
    def index():
        if (root / "index.html").is_file():
            return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})
        return RedirectResponse("/docs")

    @application.get("/{path:path}", include_in_schema=False)
    def client_route(path: str):
        if path.startswith(("api/", "assets/")):
            raise HTTPException(404, "Not found")
        target = (root / path).resolve()
        if root in target.parents and target.is_file():
            return FileResponse(target)
        if (root / "index.html").is_file():
            return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})
        raise HTTPException(404, "Build the web client to use the workspace")

    return application


app = create_app()
