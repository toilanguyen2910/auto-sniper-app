"""Task management REST API endpoints."""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from app.broadcaster import LogBroadcaster
from app.presets import PresetModel, get_all_presets
from app.storage import EncryptedStorage
from sniper.engine import SniperEngine, TaskAlreadyRunningError, TaskNotFoundError
from sniper.models import LogEntry, TaskModel, TaskStatus

logger = logging.getLogger("app.routers.tasks")

router = APIRouter(tags=["tasks"])


# --- Request & Response Models ---


class TaskResponse(TaskModel):
    """Outgoing response model with credentials masked."""

    @classmethod
    def from_task(cls, task: TaskModel) -> "TaskResponse":
        """Serialize task and mask sensitive password credentials."""
        data = task.model_dump(mode="json")
        if "credentials" in data and isinstance(data["credentials"], dict):
            if data["credentials"].get("password"):
                data["credentials"]["password"] = "********"
        return cls.model_validate(data)


class TaskCreateRequest(BaseModel):
    """Payload to create a new sniper task."""

    model_config = ConfigDict(extra="allow")

    id: Optional[str] = None
    name: str = "Untitled Task"
    target_url: str
    headless: bool = True
    manual_login: bool = False
    login_timeout_sec: int = 300
    polling_interval: float = 3.5
    jitter_sec: float = 0.5
    wait_condition: str = "domcontentloaded"
    block_media_assets: bool = True
    detection_type: str = "DOM_SELECTOR"
    detection_api_url: Optional[str] = None
    enable_dual_trigger: bool = True
    credentials: Optional[Dict[str, Any]] = None
    selectors: Optional[Dict[str, Any]] = None
    api_trigger: Optional[Dict[str, Any]] = None


class TaskUpdateRequest(BaseModel):
    """Payload to update an existing sniper task."""

    model_config = ConfigDict(extra="allow")

    name: Optional[str] = None
    target_url: Optional[str] = None
    headless: Optional[bool] = None
    manual_login: Optional[bool] = None
    login_timeout_sec: Optional[int] = None
    polling_interval: Optional[float] = None
    jitter_sec: Optional[float] = None
    wait_condition: Optional[str] = None
    block_media_assets: Optional[bool] = None
    detection_type: Optional[str] = None
    detection_api_url: Optional[str] = None
    enable_dual_trigger: Optional[bool] = None
    credentials: Optional[Dict[str, Any]] = None
    selectors: Optional[Dict[str, Any]] = None
    api_trigger: Optional[Dict[str, Any]] = None


# --- Dependency Injection Helpers ---


def get_storage(request: Request) -> EncryptedStorage:
    if not hasattr(request.app.state, "storage") or request.app.state.storage is None:
        request.app.state.storage = EncryptedStorage()
    return request.app.state.storage


def get_broadcaster(request: Request) -> LogBroadcaster:
    if not hasattr(request.app.state, "broadcaster") or request.app.state.broadcaster is None:
        request.app.state.broadcaster = LogBroadcaster()
    return request.app.state.broadcaster


def get_engine(request: Request) -> SniperEngine:
    if not hasattr(request.app.state, "engine") or request.app.state.engine is None:
        request.app.state.engine = SniperEngine()
    return request.app.state.engine


# --- API Routes ---


@router.get("/api/health")
async def health_check(
    request: Request,
    engine: SniperEngine = Depends(get_engine),
):
    """Health check & status endpoint."""
    active_count = 0
    if hasattr(engine, "_active_tasks"):
        active_count = len(engine._active_tasks)
    return {
        "status": "ok",
        "service": "auto_sniper_backend",
        "version": "1.0.0",
        "active_tasks": active_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/presets", response_model=List[PresetModel])
async def list_presets():
    """Return all built-in platform presets."""
    return get_all_presets()


@router.get("/api/tasks", response_model=List[TaskResponse])
async def list_tasks(storage: EncryptedStorage = Depends(get_storage)):
    """List all persisted tasks with passwords masked."""
    tasks = storage.load_all()
    return [TaskResponse.from_task(t) for t in tasks.values()]


@router.post(
    "/api/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    payload: TaskCreateRequest,
    storage: EncryptedStorage = Depends(get_storage),
):
    """Create a new sniper task and save it securely to encrypted storage."""
    data = payload.model_dump(exclude_none=True)
    if not data.get("id"):
        data["id"] = str(uuid.uuid4())

    data["status"] = TaskStatus.PENDING
    task = TaskModel.model_validate(data)
    storage.save_task(task)
    logger.info("Created task %s ('%s')", task.id, task.name)
    return TaskResponse.from_task(task)


@router.get("/api/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    storage: EncryptedStorage = Depends(get_storage),
):
    """Retrieve a single task by ID with masked credentials."""
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    return TaskResponse.from_task(task)


@router.put("/api/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: str,
    payload: TaskUpdateRequest,
    storage: EncryptedStorage = Depends(get_storage),
    engine: SniperEngine = Depends(get_engine),
):
    """Update task configuration. Rejects edits if task is currently active."""
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )

    # Check if running in engine
    try:
        current_status = engine.get_task_status(task_id)
        if current_status in (
            TaskStatus.POLLING,
            TaskStatus.SNIPING,
            TaskStatus.WAITING_LOGIN,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot update task {task_id} while running ({current_status}). Stop it first.",
            )
    except TaskNotFoundError:
        pass

    update_dict = payload.model_dump(exclude_unset=True)

    # Preserve password if client sent masked string
    if "credentials" in update_dict and isinstance(update_dict["credentials"], dict):
        new_creds = update_dict["credentials"]
        if new_creds.get("password") == "********":
            # Retain original password
            new_creds["password"] = task.credentials.password
        update_dict["credentials"] = new_creds

    current_data = task.model_dump()
    current_data.update(update_dict)
    current_data["updated_at"] = datetime.now(timezone.utc)

    updated_task = TaskModel.model_validate(current_data)
    storage.save_task(updated_task)
    logger.info("Updated task %s ('%s')", updated_task.id, updated_task.name)
    return TaskResponse.from_task(updated_task)


@router.delete("/api/tasks/{task_id}")
async def delete_task(
    task_id: str,
    storage: EncryptedStorage = Depends(get_storage),
    engine: SniperEngine = Depends(get_engine),
):
    """Stop active execution and permanently remove task from encrypted storage."""
    # Attempt to stop if currently registered in engine
    try:
        engine.stop_task(task_id)
    except Exception:
        pass

    existed = storage.delete_task(task_id)
    if not existed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )

    logger.info("Deleted task %s", task_id)
    return {"status": "deleted", "task_id": task_id}


@router.post("/api/tasks/{task_id}/start")
async def start_task(
    task_id: str,
    storage: EncryptedStorage = Depends(get_storage),
    broadcaster: LogBroadcaster = Depends(get_broadcaster),
    engine: SniperEngine = Depends(get_engine),
):
    """Start task execution in SniperEngine with live log & status hooks."""
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )

    # Callback closures that route to broadcaster and update encrypted store
    async def log_callback(entry: LogEntry) -> None:
        await broadcaster.broadcast_log(entry)

    async def status_callback(tid: str, new_status: TaskStatus) -> None:
        await broadcaster.emit_status_change(tid, new_status)
        current = storage.get_task(tid)
        if current:
            current.status = new_status
            storage.save_task(current)

    try:
        await engine.start_task(
            task,
            log_callback=log_callback,
            status_callback=status_callback,
        )
    except TaskAlreadyRunningError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task {task_id} is already running",
        )
    except Exception as e:
        logger.error("Failed to start task %s: %s", task_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error starting task: {e}",
        )

    # Persist initial status
    task.status = TaskStatus.PENDING
    storage.save_task(task)

    return {"status": "started", "task_id": task_id}


@router.post("/api/tasks/{task_id}/stop")
async def stop_task(
    task_id: str,
    storage: EncryptedStorage = Depends(get_storage),
    broadcaster: LogBroadcaster = Depends(get_broadcaster),
    engine: SniperEngine = Depends(get_engine),
):
    """Stop an active sniper task."""
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )

    try:
        engine.stop_task(task_id)
    except TaskNotFoundError:
        pass

    task.status = TaskStatus.STOPPED
    storage.save_task(task)

    await broadcaster.emit_status_change(
        task_id,
        TaskStatus.STOPPED,
        message="Task stopped by user request.",
    )

    return {"status": "stopped", "task_id": task_id}


@router.post("/api/tasks/stop-all")
async def stop_all_tasks(
    storage: EncryptedStorage = Depends(get_storage),
    broadcaster: LogBroadcaster = Depends(get_broadcaster),
    engine: SniperEngine = Depends(get_engine),
):
    """Emergency stop all active sniper tasks."""
    try:
        engine.stop_all()
    except Exception as e:
        logger.warning("Error stopping all in engine: %s", e)

    tasks = storage.load_all()
    stopped_count = 0
    for t in tasks.values():
        if t.status in (
            TaskStatus.POLLING,
            TaskStatus.SNIPING,
            TaskStatus.WAITING_LOGIN,
            TaskStatus.PENDING,
        ):
            t.status = TaskStatus.STOPPED
            stopped_count += 1
    storage.save_all(tasks)

    await broadcaster.emit_log(
        "global",
        "WARNING",
        f"All tasks stopped ({stopped_count} tasks transitioned to STOPPED).",
    )

    return {"status": "all_stopped", "stopped_count": stopped_count}


@router.post("/api/tasks/{task_id}/confirm_login")
async def confirm_login(
    task_id: str,
    storage: EncryptedStorage = Depends(get_storage),
    engine: SniperEngine = Depends(get_engine),
):
    """Signal completion of manual login or Captcha solving for a task in WAITING_LOGIN."""
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )

    confirmed = engine.confirm_login(task_id)
    if not confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task {task_id} is not currently waiting for manual login.",
        )

    return {"status": "login_confirmed", "task_id": task_id}
