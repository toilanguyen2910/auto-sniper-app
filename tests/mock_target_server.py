"""
Mock Target Server (Port 8081)
Simulates a real-world registration portal (e.g. VAU Events / Course Registration)
with dynamic slot release, in-page HTML UI, and internal JSON API endpoints.
"""

import argparse
import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("mock_target_server")


class MockPortalState:
    """Thread-safe state manager for the mock registration portal."""

    def __init__(self, total_slots: int = 50, initial_full: bool = True, auto_release_after: float = 0.0):
        self.lock = threading.Lock()
        self.total_slots = total_slots
        self.occupied_slots = total_slots if initial_full else max(0, total_slots - 1)
        self.is_full = initial_full
        self.auto_release_after = auto_release_after
        self.created_at = time.time()
        self.registered_participants: List[Dict[str, Any]] = []
        self.successful_method: Optional[str] = None  # "api" | "ui" | "dual"
        self.request_history: List[Dict[str, Any]] = []

    def check_dynamic_release(self) -> bool:
        """Dynamically frees 1 slot if auto_release_after seconds have passed."""
        with self.lock:
            if self.is_full and self.auto_release_after > 0:
                elapsed = time.time() - self.created_at
                if elapsed >= self.auto_release_after:
                    self.occupied_slots = max(0, self.total_slots - 1)
                    self.is_full = False
                    logger.info("Dynamic slot release triggered: elapsed=%.2fs", elapsed)
                    return True
            return False

    def release_slot(self) -> Dict[str, Any]:
        """Manually release 1 slot immediately."""
        with self.lock:
            self.occupied_slots = max(0, self.occupied_slots - 1)
            self.is_full = self.occupied_slots >= self.total_slots
            logger.info("Slot manually released. Occupied: %d/%d", self.occupied_slots, self.total_slots)
            return {
                "occupied_slots": self.occupied_slots,
                "total_slots": self.total_slots,
                "is_full": self.is_full,
                "available_slots": self.total_slots - self.occupied_slots,
            }

    def reset(self, total_slots: int = 50, initial_full: bool = True, auto_release_after: float = 0.0) -> None:
        """Reset state back to initial full state."""
        with self.lock:
            self.total_slots = total_slots
            self.occupied_slots = total_slots if initial_full else max(0, total_slots - 1)
            self.is_full = initial_full
            self.auto_release_after = auto_release_after
            self.created_at = time.time()
            self.registered_participants = []
            self.successful_method = None
            self.request_history = []
            logger.info("MockPortalState reset to occupied: %d/%d, is_full=%s", self.occupied_slots, self.total_slots, self.is_full)

    def register(self, session_ids: List[str], method: str = "api", user_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Attempt to register a slot."""
        self.check_dynamic_release()
        with self.lock:
            if self.is_full or self.occupied_slots >= self.total_slots:
                logger.warning("Registration rejected: Portal is full (%d/%d)", self.occupied_slots, self.total_slots)
                return {
                    "success": False,
                    "message": "Sự kiện đã hết chỗ! Đã đạt số lượng tối đa.",
                    "status_code": 409,
                }

            # Increment slot
            self.occupied_slots += 1
            if self.occupied_slots >= self.total_slots:
                self.is_full = True

            # Record method (detect dual trigger if both methods registered in rapid succession)
            if self.successful_method and self.successful_method != method:
                self.successful_method = "dual"
            else:
                self.successful_method = method

            reservation_id = f"res_{uuid4().hex[:8]}"
            entry = {
                "reservation_id": reservation_id,
                "session_ids": session_ids,
                "method": method,
                "registered_at": time.time(),
                "user": user_info or {},
            }
            self.registered_participants.append(entry)
            logger.info("Registration SUCCESS via %s! ResID=%s, Occupied: %d/%d", method, reservation_id, self.occupied_slots, self.total_slots)
            return {
                "success": True,
                "message": "Đăng ký thành công!",
                "reservation_id": reservation_id,
                "occupied_slots": self.occupied_slots,
                "total_slots": self.total_slots,
                "status_code": 200,
            }

    def get_status_dict(self) -> Dict[str, Any]:
        """Get snapshot of current portal state."""
        self.check_dynamic_release()
        with self.lock:
            return {
                "total_slots": self.total_slots,
                "occupied_slots": self.occupied_slots,
                "available_slots": max(0, self.total_slots - self.occupied_slots),
                "is_full": self.is_full,
                "auto_release_after": self.auto_release_after,
                "elapsed_seconds": round(time.time() - self.created_at, 2),
                "successful_method": self.successful_method,
                "registered_participants_count": len(self.registered_participants),
                "registered_participants": list(self.registered_participants),
            }


# Global state instance
mock_state = MockPortalState(total_slots=50, initial_full=True, auto_release_after=0.0)


class RegisterPayload(BaseModel):
    sessionIds: List[str] = Field(default_factory=lambda: ["slot-alpha"])


class ConfigurePayload(BaseModel):
    total_slots: Optional[int] = None
    occupied_slots: Optional[int] = None
    is_full: Optional[bool] = None
    auto_release_after: Optional[float] = None


def render_html_portal() -> str:
    """Generate HTML registration portal reflecting current capacity state."""
    mock_state.check_dynamic_release()
    status = mock_state.get_status_dict()
    is_full = status["is_full"]
    occupied = status["occupied_slots"]
    total = status["total_slots"]

    if is_full:
        banner_class = "status-full"
        banner_text = f"Trạng thái: Đã hết chỗ ({occupied}/{total}) - Đã đạt số lượng tối đa"
        slot_text = f"Đã đầy ({occupied}/{total} chỗ)"
        button_html = '<button id="btn-register" disabled class="btn-disabled bg-gray-600 text-gray-300 font-bold py-3 px-6 rounded-lg cursor-not-allowed">Hết chỗ</button>'
    else:
        banner_class = "status-available"
        banner_text = f"Trạng thái: Còn chỗ trống ({occupied}/{total}) - Đang mở đăng ký"
        slot_text = f"Còn {total - occupied} chỗ trống ({occupied}/{total} chỗ)"
        button_html = '<button id="btn-register" class="btn-register bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3 px-6 rounded-lg shadow-lg transition duration-200" onclick="openRegisterModal()">Đăng ký tham gia</button>'

    html_content = f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VAUVERSE 2026 - Lễ Hội Công Nghệ & Khởi Nghiệp</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .status-full {{ background-color: rgba(239, 68, 68, 0.2); border: 1px solid rgb(239, 68, 68); color: rgb(248, 113, 113); }}
        .status-available {{ background-color: rgba(16, 185, 129, 0.2); border: 1px solid rgb(16, 185, 129); color: rgb(52, 211, 153); }}
    </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col items-center justify-center p-4">
    <div class="max-w-xl w-full bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl p-8 space-y-6">
        <div class="text-center space-y-2">
            <span class="text-xs uppercase tracking-widest text-emerald-400 font-semibold">Sự kiện chính thức</span>
            <h1 class="text-3xl font-extrabold text-white">VAUVERSE 2026</h1>
            <p class="text-sm text-slate-400">Lễ Hội Công Nghệ & Khởi Nghiệp Sinh Viên VAU</p>
        </div>

        <div id="status-banner" class="{banner_class} rounded-xl p-4 text-center font-medium">
            {banner_text}
        </div>

        <div class="bg-slate-800/60 rounded-xl p-4 flex justify-between items-center border border-slate-700/60">
            <div>
                <span class="text-xs text-slate-400 block">Số lượng vé</span>
                <span id="slot-info" class="text-base font-semibold text-slate-200">{slot_text}</span>
            </div>
            <div id="action-container">
                {button_html}
            </div>
        </div>

        <!-- Registration Modal (Hidden by default or toggled) -->
        <div id="register-modal" class="hidden bg-slate-800 border border-slate-700 rounded-xl p-6 space-y-4">
            <h3 class="text-lg font-bold text-white">Chọn buổi tham gia</h3>
            <div class="space-y-3">
                <label class="flex items-center space-x-3 p-3 rounded-lg bg-slate-900/60 hover:bg-slate-900 cursor-pointer border border-slate-700">
                    <input type="checkbox" id="session-pick-slot-alpha" name="session" value="slot-alpha" class="w-5 h-5 text-emerald-500 rounded border-slate-700 focus:ring-emerald-400" checked>
                    <span class="text-sm font-medium text-slate-200">Buổi 1: Khai mạc VAUVERSE & Workshop AI (slot-alpha)</span>
                </label>
                <label class="flex items-center space-x-3 p-3 rounded-lg bg-slate-900/60 hover:bg-slate-900 cursor-pointer border border-slate-700">
                    <input type="checkbox" id="session-pick-cmuy26okv0o3d2mo4fyiepdc1" name="session" value="cmuy26okv0o3d2mo4fyiepdc1" class="w-5 h-5 text-emerald-500 rounded border-slate-700 focus:ring-emerald-400">
                    <span class="text-sm font-medium text-slate-200">Buổi 2: Chung kết Hackathon Quốc Gia</span>
                </label>
            </div>
            <div class="flex justify-end space-x-3 pt-2">
                <button type="button" onclick="closeRegisterModal()" class="px-4 py-2 text-sm text-slate-400 hover:text-white">Hủy</button>
                <button id="btn-confirm-register" onclick="submitRegistration()" class="px-5 py-2 text-sm font-semibold bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg shadow">Xác nhận đăng ký</button>
            </div>
        </div>

        <div id="result-message" class="hidden rounded-xl p-4 text-center font-bold"></div>
    </div>

    <script>
        function openRegisterModal() {{
            const modal = document.getElementById('register-modal');
            if (modal) modal.classList.remove('hidden');
        }}

        function closeRegisterModal() {{
            const modal = document.getElementById('register-modal');
            if (modal) modal.classList.add('hidden');
        }}

        async function submitRegistration() {{
            const checkboxes = document.querySelectorAll('#register-modal input[type="checkbox"]:checked');
            const sessionIds = Array.from(checkboxes).map(cb => cb.value);
            if (sessionIds.length === 0) {{
                sessionIds.push('slot-alpha');
            }}

            try {{
                const res = await fetch('/api/events/mock-event-vau/ui-register', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ sessionIds: sessionIds }})
                }});
                const data = await res.json();
                const resultDiv = document.getElementById('result-message');
                resultDiv.classList.remove('hidden');

                if (res.ok && data.success) {{
                    resultDiv.className = 'status-available rounded-xl p-4 text-center font-bold text-emerald-400';
                    resultDiv.innerText = '🎉 ' + (data.message || 'Đăng ký thành công!');
                    closeRegisterModal();
                    document.getElementById('action-container').innerHTML = '<span class="text-emerald-400 font-bold">✓ Đã đăng ký thành công</span>';
                }} else {{
                    resultDiv.className = 'status-full rounded-xl p-4 text-center font-bold text-rose-400';
                    resultDiv.innerText = '❌ ' + (data.message || 'Đăng ký thất bại');
                }}
            }} catch (err) {{
                console.error(err);
            }}
        }}
    </script>
</body>
</html>"""
    return html_content


def create_mock_app() -> FastAPI:
    """Create FastAPI application for Mock Target Server."""
    app = FastAPI(title="Mock Registration Target Portal", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse)
    @app.get("/events/mock-event-vau", response_class=HTMLResponse)
    @app.get("/events/cmuy26oks0o3c2mo4k39w31v5", response_class=HTMLResponse)
    async def get_portal_html():
        """Serves HTML registration portal page."""
        return HTMLResponse(content=render_html_portal())

    @app.post("/api/events/mock-event-vau/register")
    @app.post("/api/events/cmuy26oks0o3c2mo4k39w31v5/register")
    @app.post("/api/events/{event_id}/register")
    async def api_register(payload: RegisterPayload, request: Request):
        """Internal API endpoint that accepts JSON {"sessionIds": ["..."]}."""
        user_agent = request.headers.get("user-agent", "")
        client_host = request.client.host if request.client else "unknown"
        res = mock_state.register(
            session_ids=payload.sessionIds,
            method="api",
            user_info={"user_agent": user_agent, "ip": client_host},
        )
        if not res["success"]:
            raise HTTPException(status_code=res["status_code"], detail=res)
        return JSONResponse(status_code=200, content=res)

    @app.post("/api/events/mock-event-vau/ui-register")
    async def ui_register(payload: RegisterPayload, request: Request):
        """Internal endpoint invoked via UI button click."""
        user_agent = request.headers.get("user-agent", "")
        res = mock_state.register(
            session_ids=payload.sessionIds,
            method="ui",
            user_info={"user_agent": user_agent, "ui": True},
        )
        if not res["success"]:
            raise HTTPException(status_code=res["status_code"], detail=res)
        return JSONResponse(status_code=200, content=res)

    @app.post("/mock/release-slot")
    async def trigger_release_slot():
        """Test helper endpoint to immediately free 1 slot."""
        res = mock_state.release_slot()
        return JSONResponse(status_code=200, content={"status": "slot_released", **res})

    @app.get("/mock/status")
    @app.get("/mock/state")
    async def get_status():
        """Returns current slot count, registered participants, and operational status."""
        return JSONResponse(status_code=200, content=mock_state.get_status_dict())

    @app.post("/mock/reset")
    async def trigger_reset():
        """Resets server state to full capacity (50/50)."""
        mock_state.reset(total_slots=50, initial_full=True, auto_release_after=0.0)
        return JSONResponse(
            status_code=200,
            content={"status": "reset", "occupied_slots": 50, "total_slots": 50, "is_full": True},
        )

    @app.post("/mock/configure")
    async def trigger_configure(config: ConfigurePayload):
        """Configure mock server parameters dynamically."""
        with mock_state.lock:
            if config.total_slots is not None:
                mock_state.total_slots = config.total_slots
            if config.occupied_slots is not None:
                mock_state.occupied_slots = config.occupied_slots
            if config.is_full is not None:
                mock_state.is_full = config.is_full
            if config.auto_release_after is not None:
                mock_state.auto_release_after = config.auto_release_after
                mock_state.created_at = time.time()
        return JSONResponse(status_code=200, content=mock_state.get_status_dict())

    @app.get("/mock/health")
    async def mock_health():
        return {"status": "ok", "service": "mock_target_server", "port": 8081}

    return app


app = create_mock_app()


class MockServerHandle:
    """Handle to control background uvicorn server in tests."""

    def __init__(self, server: uvicorn.Server, thread: threading.Thread):
        self.server = server
        self.thread = thread

    def stop(self):
        """Stop uvicorn server and join thread."""
        self.server.should_exit = True
        self.thread.join(timeout=3.0)


def start_mock_server(host: str = "127.0.0.1", port: int = 8081, auto_release_after: float = 0.0) -> MockServerHandle:
    """
    Start mock target server in a daemon thread.
    Useful for pytest fixtures or background execution.
    """
    mock_state.reset(total_slots=50, initial_full=True, auto_release_after=auto_release_after)
    config = uvicorn.Config(app=app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to bind
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.05)

    return MockServerHandle(server=server, thread=thread)


def main():
    parser = argparse.ArgumentParser(description="Mock Registration Target Portal Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8081, help="Port (default: 8081)")
    parser.add_argument("--auto-release", type=float, default=0.0, help="Seconds before auto releasing 1 slot (0 = disabled)")
    parser.add_argument("--initial-full", action="store_true", default=True, help="Start in full state (50/50)")
    args = parser.parse_args()

    mock_state.reset(total_slots=50, initial_full=args.initial_full, auto_release_after=args.auto_release)
    print(f"[*] Starting Mock Target Server on http://{args.host}:{args.port}")
    print(f"[*] Initial capacity: {mock_state.occupied_slots}/{mock_state.total_slots} (Full={mock_state.is_full})")
    if args.auto_release > 0:
        print(f"[*] Dynamic auto-release scheduled in {args.auto_release}s")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
