"""Local BountyOS HTTP interface and static dashboard."""

from __future__ import annotations

from contextlib import asynccontextmanager
import inspect
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .store import Store


PROJECT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_agents: StrictInt | None = Field(default=None, ge=1, le=10)
    autoscale: StrictBool | None = None
    auto_submit: StrictBool | None = None
    poll_interval_minutes: StrictInt | None = Field(default=None, ge=5, le=1440)
    daily_budget_sek: StrictInt | None = Field(default=None, ge=0, le=0)


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str


class PaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1, max_length=100)
    amount_sek: float = Field(gt=0, le=100_000_000, allow_inf_nan=False, strict=True)
    reference: str = Field(min_length=1, max_length=200)


async def invoke(function, *args):
    if inspect.iscoroutinefunction(function):
        return await function(*args)
    result = await run_in_threadpool(function, *args)
    return await result if inspect.isawaitable(result) else result


def create_app(data_dir: str | Path | None = None, engine_factory=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        state_dir = Path(data_dir or os.environ.get("BOUNTYOS_DATA_DIR", PROJECT_DIR / "data"))
        store = Store(state_dir)
        store.import_research(PROJECT_DIR / "pilot" / "research.json")
        if engine_factory is None:
            from .engine import Engine
            engine = Engine(store)
        else:
            engine = engine_factory(store)
        application.state.store = store
        application.state.engine = engine
        await invoke(engine.start)
        try:
            yield
        finally:
            await invoke(engine.stop)

    application = FastAPI(title="BountyOS", version="0.1.0", lifespan=lifespan)
    allowed_hosts = ["127.0.0.1", "localhost", "[::1]"]
    if engine_factory is not None:
        allowed_hosts.append("testserver")
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts, www_redirect=False)

    @application.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                except ValueError:
                    return JSONResponse(status_code=403, content={"ok": False, "detail": "Anropet har en ogiltig avsändaradress."})
                host = request.headers.get("host", "").lower()
                if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != host or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                    return JSONResponse(status_code=403, content={"ok": False, "detail": "Anrop från en annan webbplats är inte tillåtna."})
            elif request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse(status_code=403, content={"ok": False, "detail": "Anrop från en annan webbplats är inte tillåtna."})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"ok": False, "detail": str(exc)})

    @application.get("/api/health")
    async def health():
        return {"ok": True, "service": "BountyOS", "daily_budget_sek": 0}

    @application.get("/api/state")
    async def state(request: Request):
        connections = await invoke(request.app.state.engine.connection_status)
        return await invoke(request.app.state.store.get_state, connections)

    @application.put("/api/settings")
    async def settings(payload: SettingsUpdate, request: Request):
        changes = payload.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise ValueError("Inställningar får inte vara null.")
        settings = await invoke(request.app.state.store.update_settings, changes)
        await invoke(request.app.state.store.log, "Inställningar sparade. Driftbudgeten är fortfarande 0 kr.")
        return {"ok": True, "settings": settings}

    @application.post("/api/control")
    async def control(payload: ControlRequest, request: Request):
        if payload.action not in {"start", "pause"}:
            raise ValueError("action måste vara start eller pause.")
        running = payload.action == "start"
        await invoke(request.app.state.store.update_settings, {"running": running})
        await invoke(request.app.state.store.log, "Automatisk körning startad." if running else "Automatisk körning pausad; ett redan påbörjat steg kan slutföras.")
        if running:
            await invoke(request.app.state.engine.scan_now)
        return {"ok": True, "running": running}

    @application.post("/api/scan")
    async def scan(request: Request):
        result = await invoke(request.app.state.engine.scan_now)
        return {"ok": True, "scan": result}

    @application.post("/api/connections/check")
    async def check_connections(request: Request):
        engine = request.app.state.engine
        result = await invoke(engine.check_connections)
        return {"ok": True, "connections": result}

    @application.post("/api/tasks/{task_id}/retry")
    async def retry(task_id: str, request: Request):
        store = request.app.state.store
        task = await invoke(store.get_task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Uppdraget finns inte.")
        if task["status"] not in {"blocked", "failed", "skipped"}:
            raise HTTPException(status_code=409, detail="Endast blockerade, bortvalda eller misslyckade uppdrag kan granskas på nytt.")
        await run_in_threadpool(store.update_task, task_id, status="discovered", reason="Väntar på ny granskning.")
        await invoke(store.log, f"Ny granskning köad: {task['title']}.")
        await invoke(request.app.state.engine.scan_now)
        return {"ok": True}

    @application.get("/api/tasks/{task_id}/artifact", response_class=PlainTextResponse)
    async def artifact(task_id: str, request: Request):
        store = request.app.state.store
        task = await invoke(store.get_task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Uppdraget finns inte.")
        if not task.get("artifact_path"):
            raise HTTPException(status_code=404, detail="Uppdraget har ingen leveransfil ännu.")
        path = Path(task["artifact_path"])
        if not path.is_absolute():
            path = store.data_dir / path
        path = path.resolve()
        if not path.is_relative_to(store.data_dir) or not path.is_file():
            raise HTTPException(status_code=404, detail="Leveransfilen kunde inte hittas i programmets datamapp.")
        if path.stat().st_size > 2_000_000:
            raise HTTPException(status_code=413, detail="Filen är för stor för att förhandsvisas; öppna den från datamappen.")
        text = await run_in_threadpool(path.read_text, encoding="utf-8", errors="replace")
        return PlainTextResponse(text)

    @application.post("/api/payments")
    async def payments(payload: PaymentRequest, request: Request):
        payment_id = await invoke(request.app.state.store.record_payment, payload.task_id, payload.amount_sek, payload.reference)
        return {"ok": True, "payment_id": payment_id, "verification": "manual"}

    application.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")

    @application.get("/", include_in_schema=False)
    @application.get("/agents", include_in_schema=False)
    @application.get("/tasks", include_in_schema=False)
    @application.get("/earnings", include_in_schema=False)
    @application.get("/settings", include_in_schema=False)
    async def dashboard():
        index = STATIC_DIR / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=503, detail="Dashboardens filer saknas.")
        return FileResponse(index)

    return application


app = create_app()
