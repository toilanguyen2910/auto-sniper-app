"""WebSocket and SSE streaming endpoints for real-time task logs."""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.broadcaster import LogBroadcaster

logger = logging.getLogger("app.routers.ws")

router = APIRouter(tags=["websockets"])


@router.websocket("/ws/tasks/{task_id}/logs")
async def ws_task_logs(websocket: WebSocket, task_id: str):
    """WebSocket endpoint streaming live logs and status events for a specific task."""
    broadcaster: LogBroadcaster = websocket.app.state.broadcaster
    await broadcaster.connect(websocket, task_id=task_id)
    try:
        while True:
            # Keep socket open and receive any ping/pong/client messages
            await websocket.receive_text()
    except (WebSocketDisconnect, ConnectionResetError):
        pass
    except Exception as e:
        logger.debug("Task websocket error for %s: %s", task_id, e)
    finally:
        await broadcaster.disconnect(websocket, task_id=task_id)


@router.websocket("/ws/logs")
async def ws_global_logs(websocket: WebSocket):
    """WebSocket endpoint streaming global logs and events across all tasks."""
    broadcaster: LogBroadcaster = websocket.app.state.broadcaster
    await broadcaster.connect(websocket, task_id=None)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, ConnectionResetError):
        pass
    except Exception as e:
        logger.debug("Global websocket error: %s", e)
    finally:
        await broadcaster.disconnect(websocket, task_id=None)


@router.get("/api/tasks/{task_id}/logs/stream")
async def sse_task_logs_stream(request: Request, task_id: str):
    """Server-Sent Events (SSE) fallback stream for a specific task."""
    broadcaster: LogBroadcaster = request.app.state.broadcaster

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        sub_id = await broadcaster.register_sse_queue(task_id, queue)
        try:
            # 1. Replay historical buffered items
            history = broadcaster.get_history(task_id)
            for item in history:
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

            # 2. Stream live events as they occur
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive heartbeat comment
                    yield ": ping\n\n"
        finally:
            await broadcaster.unregister_sse_queue(task_id, sub_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/logs/stream")
async def sse_global_logs_stream(request: Request):
    """Server-Sent Events (SSE) fallback stream for global logs."""
    broadcaster: LogBroadcaster = request.app.state.broadcaster

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        sub_id = await broadcaster.register_sse_queue(None, queue)
        try:
            history = broadcaster.get_global_history()
            for item in history:
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            await broadcaster.unregister_sse_queue(None, sub_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
