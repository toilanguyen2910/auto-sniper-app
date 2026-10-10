"""FastAPI Application Server initialization, lifespan management, and static file serving."""

from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.broadcaster import LogBroadcaster
from app.routers.tasks import router as tasks_router
from app.routers.ws import router as ws_router
from app.storage import EncryptedStorage
from sniper.engine import SniperEngine
from sniper.models import TaskStatus

logger = logging.getLogger("app.server")

# Resolve directories
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage lifecycle startup initialization and graceful shutdown."""
    logger.info("Initializing Auto-Sniper backend services...")

    # 1. Ensure storage, broadcaster, and engine instances exist on app.state
    if not hasattr(app.state, "storage") or app.state.storage is None:
        app.state.storage = EncryptedStorage()
    if not hasattr(app.state, "broadcaster") or app.state.broadcaster is None:
        app.state.broadcaster = LogBroadcaster()
    if not hasattr(app.state, "engine") or app.state.engine is None:
        app.state.engine = SniperEngine()

    storage = app.state.storage
    broadcaster = app.state.broadcaster
    engine = app.state.engine

    # Sanitize active tasks on startup
    try:
        tasks = storage.load_all()
        sanitized = 0
        for task in tasks.values():
            if task.status in (
                TaskStatus.POLLING,
                TaskStatus.SNIPING,
                TaskStatus.WAITING_LOGIN,
                TaskStatus.PENDING,
            ):
                task.status = TaskStatus.STOPPED
                sanitized += 1
        if sanitized > 0:
            storage.save_all(tasks)
            logger.info("Sanitized %d active tasks to STOPPED on startup.", sanitized)
    except Exception as e:
        logger.warning("Error during initial task sanitization: %s", e)

    logger.info("Auto-Sniper backend services ready.")
    yield

    # Shutdown sequence
    logger.info("Shutting down Auto-Sniper backend...")
    try:
        engine.stop_all()
    except Exception as e:
        logger.debug("Error stopping tasks during shutdown: %s", e)

    try:
        await engine.browser_manager.shutdown()
    except Exception as e:
        logger.debug("Error shutting down browser manager: %s", e)

    logger.info("Auto-Sniper backend shutdown complete.")


def create_app() -> FastAPI:
    """FastAPI application factory."""
    application = FastAPI(
        title="Web Auto-Sniper API",
        version="1.0.0",
        description="High-Speed Multi-Task Web Automation & Registration Sniper API",
        lifespan=lifespan,
    )

    # Initialize default state instances
    application.state.storage = EncryptedStorage()
    application.state.broadcaster = LogBroadcaster()
    application.state.engine = SniperEngine()

    # CORS configuration allowing localhost origins
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "*",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    application.include_router(tasks_router)
    application.include_router(ws_router)

    # Prepare static directory
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        index_file.write_text(
            """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Web Auto-Sniper Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen flex items-center justify-center">
    <div class="text-center space-y-4 p-8 bg-slate-900 rounded-2xl border border-slate-800 shadow-2xl max-w-md">
        <h1 class="text-3xl font-extrabold text-emerald-400">Web Auto-Sniper</h1>
        <p class="text-slate-400">High-speed slot/ticket/course registration engine.</p>
        <div class="p-3 bg-slate-800 rounded-lg text-xs font-mono text-emerald-300">
            Backend API: <span class="text-white font-bold">ONLINE (Port 8000)</span>
        </div>
    </div>
</body>
</html>""",
            encoding="utf-8",
        )

    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @application.get("/", response_class=FileResponse)
    async def get_index():
        """Serve Dashboard SPA root HTML."""
        if index_file.exists():
            return FileResponse(str(index_file))
        return HTMLResponse("<h1>Web Auto-Sniper Dashboard</h1>")

    return application


app = create_app()
