"""Real-time log and status broadcaster over WebSockets and Server-Sent Events (SSE).

Maintains bounded in-memory ring buffers (deque maxlen=500 per task) and supports
immediate history replay upon client connection.
"""

import asyncio
from collections import deque
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Set, Union
import uuid

from fastapi import WebSocket

from sniper.models import LogEntry, TaskStatus

logger = logging.getLogger("app.broadcaster")


class LogBroadcaster:
    """Manages active WebSocket subscriptions and buffered log broadcasting."""

    def __init__(self, task_buffer_size: int = 500, global_buffer_size: int = 1000) -> None:
        self._task_buffer_size = task_buffer_size
        self._global_buffer_size = global_buffer_size

        # In-memory ring buffers
        self._task_buffers: Dict[str, deque] = {}
        self._global_buffer: deque = deque(maxlen=global_buffer_size)

        # Active WebSocket connections
        self._task_subscribers: Dict[str, Set[WebSocket]] = {}
        self._global_subscribers: Set[WebSocket] = set()

        # Active SSE queues: subscriber_id -> (queue, task_id)
        self._sse_queues: Dict[str, asyncio.Queue] = {}
        self._task_sse_map: Dict[str, Set[str]] = {}

        self._lock = asyncio.Lock()

    def get_history(self, task_id: str) -> List[Dict[str, Any]]:
        """Return shallow copy list of buffered log/status events for a specific task."""
        if task_id in self._task_buffers:
            return list(self._task_buffers[task_id])
        return []

    def get_global_history(self) -> List[Dict[str, Any]]:
        """Return shallow copy list of global buffered log/status events."""
        return list(self._global_buffer)

    async def connect(self, websocket: WebSocket, task_id: Optional[str] = None) -> None:
        """Accept WebSocket connection, replay existing log history, and register subscriber."""
        await websocket.accept()

        # Replay history first so the client immediately populates its terminal
        if task_id:
            history = self.get_history(task_id)
        else:
            history = self.get_global_history()

        for item in history:
            try:
                await websocket.send_json(item)
            except Exception as e:
                logger.debug("Failed during history replay to websocket: %s", e)
                return

        async with self._lock:
            if task_id:
                if task_id not in self._task_subscribers:
                    self._task_subscribers[task_id] = set()
                self._task_subscribers[task_id].add(websocket)
            else:
                self._global_subscribers.add(websocket)

        logger.debug(
            "WebSocket connected for %s (total subscribers: %d)",
            f"task {task_id}" if task_id else "global",
            len(self._task_subscribers.get(task_id, set())) if task_id else len(self._global_subscribers),
        )

    async def disconnect(self, websocket: WebSocket, task_id: Optional[str] = None) -> None:
        """Unregister a disconnected WebSocket subscriber."""
        async with self._lock:
            if task_id and task_id in self._task_subscribers:
                self._task_subscribers[task_id].discard(websocket)
                if not self._task_subscribers[task_id]:
                    del self._task_subscribers[task_id]
            self._global_subscribers.discard(websocket)

    async def register_sse_queue(self, task_id: Optional[str], queue: asyncio.Queue) -> str:
        """Register an SSE listener queue and return subscription ID."""
        sub_id = str(uuid.uuid4())
        async with self._lock:
            self._sse_queues[sub_id] = queue
            if task_id:
                if task_id not in self._task_sse_map:
                    self._task_sse_map[task_id] = set()
                self._task_sse_map[task_id].add(sub_id)
        return sub_id

    async def unregister_sse_queue(self, task_id: Optional[str], sub_id: str) -> None:
        """Unregister an SSE listener queue."""
        async with self._lock:
            if sub_id in self._sse_queues:
                del self._sse_queues[sub_id]
            if task_id and task_id in self._task_sse_map:
                self._task_sse_map[task_id].discard(sub_id)

    async def emit_log(
        self,
        task_id: str,
        level: str,
        message: str,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Format, buffer, and broadcast a log entry to all relevant subscribers."""
        payload: Dict[str, Any] = {
            "type": "log",
            "task_id": task_id,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "iso_time": datetime.now(timezone.utc).isoformat(),
            "level": level.upper(),
            "message": message,
            "metrics": metrics or {},
        }

        # 1. Update in-memory ring buffers
        if task_id not in self._task_buffers:
            self._task_buffers[task_id] = deque(maxlen=self._task_buffer_size)
        self._task_buffers[task_id].append(payload)
        self._global_buffer.append(payload)

        # 2. Gather target WebSocket subscribers
        async with self._lock:
            targets = set(self._global_subscribers)
            if task_id in self._task_subscribers:
                targets.update(self._task_subscribers[task_id])

            # Gather target SSE queues
            sse_targets = []
            if task_id in self._task_sse_map:
                for sub_id in self._task_sse_map[task_id]:
                    if sub_id in self._sse_queues:
                        sse_targets.append(self._sse_queues[sub_id])

        # 3. Deliver to WebSockets
        dead_websockets: Set[WebSocket] = set()
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead_websockets.add(ws)

        if dead_websockets:
            async with self._lock:
                for ws in dead_websockets:
                    if task_id in self._task_subscribers:
                        self._task_subscribers[task_id].discard(ws)
                    self._global_subscribers.discard(ws)

        # 4. Deliver to SSE queues
        for q in sse_targets:
            try:
                q.put_nowait(payload)
            except Exception:
                pass

        return payload

    async def emit_status_change(
        self,
        task_id: str,
        status: Union[TaskStatus, str],
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Broadcast a task status lifecycle transition event."""
        status_str = status.value if hasattr(status, "value") else str(status)
        payload: Dict[str, Any] = {
            "type": "status_change",
            "task_id": task_id,
            "status": status_str,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "iso_time": datetime.now(timezone.utc).isoformat(),
            "message": message or f"Task status updated to {status_str}",
        }

        # Buffer status changes so clients connecting see last status
        if task_id not in self._task_buffers:
            self._task_buffers[task_id] = deque(maxlen=self._task_buffer_size)
        self._task_buffers[task_id].append(payload)
        self._global_buffer.append(payload)

        # Gather target subscribers
        async with self._lock:
            targets = set(self._global_subscribers)
            if task_id in self._task_subscribers:
                targets.update(self._task_subscribers[task_id])

            sse_targets = []
            if task_id in self._task_sse_map:
                for sub_id in self._task_sse_map[task_id]:
                    if sub_id in self._sse_queues:
                        sse_targets.append(self._sse_queues[sub_id])

        dead_websockets: Set[WebSocket] = set()
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead_websockets.add(ws)

        if dead_websockets:
            async with self._lock:
                for ws in dead_websockets:
                    if task_id in self._task_subscribers:
                        self._task_subscribers[task_id].discard(ws)
                    self._global_subscribers.discard(ws)

        for q in sse_targets:
            try:
                q.put_nowait(payload)
            except Exception:
                pass

        return payload

    async def broadcast_log(self, entry: Union[LogEntry, Dict[str, Any]]) -> Dict[str, Any]:
        """Adapter method for SniperEngine log_callback accepting LogEntry model."""
        if isinstance(entry, LogEntry):
            return await self.emit_log(
                task_id=entry.task_id,
                level=entry.level,
                message=entry.message,
                metrics=entry.metrics,
            )
        elif isinstance(entry, dict):
            return await self.emit_log(
                task_id=entry.get("task_id", "unknown"),
                level=entry.get("level", "INFO"),
                message=entry.get("message", ""),
                metrics=entry.get("metrics"),
            )
        else:
            return await self.emit_log(
                task_id=str(getattr(entry, "task_id", "unknown")),
                level=str(getattr(entry, "level", "INFO")),
                message=str(getattr(entry, "message", "")),
                metrics=getattr(entry, "metrics", None),
            )
