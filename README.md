# Web Auto-Sniper Application

A local multi-task web automation platform (FastAPI + Playwright) with real-time WebSocket log streaming, authenticated Fernet encrypted storage, and high-speed dual-trigger execution for automated slot, ticket, and course registration.

---

## 🌟 Key Architecture & Features

1. **FastAPI Asynchronous Backend (`app/`)**:
   - High-throughput asynchronous REST API for complete task CRUD and lifecycle operations (`start`, `stop`, `stop-all`, `confirm_login`).
   - Clean lifespan context manager ensuring graceful browser pool teardown and task state persistence.
   - Pydantic v2 data models with automatic credential masking (`password: "********"`) across all public API responses.

2. **Authenticated Encrypted Storage (`app/storage.py`)**:
   - AES-128-CBC + HMAC-SHA256 authenticated symmetric encryption via `cryptography.fernet`.
   - Master key auto-generation in `data/secret.key` (permission 0600 on POSIX) or configured via `SNIPER_MASTER_KEY` environment variable.
   - Atomic disk writes (`.tmp` -> `os.replace`) preventing file corruption during power loss or abrupt termination.
   - Automatic corruption recovery: malformed or unreadable data files are backed up to `tasks.enc.json.corrupt.<timestamp>` and fresh state initialized safely.
   - State sanitization: any tasks in active states (`POLLING`, `SNIPING`, `WAITING_LOGIN`) are automatically sanitized to `STOPPED` upon application boot.

3. **Real-Time Log Broadcaster & History Replay (`app/broadcaster.py`)**:
   - WebSockets at `/ws/tasks/{task_id}/logs` and `/ws/logs`.
   - In-memory bounded ring buffers (`collections.deque(maxlen=500)` per task, `maxlen=1000` global).
   - Immediate history replay upon connection: clients reconnecting or refreshing the dashboard instantly receive all previous logs.
   - Server-Sent Events (SSE) fallback stream at `/api/tasks/{task_id}/logs/stream`.

4. **Dual-Trigger Sniper Engine (`sniper/`)**:
   - Dispatches tasks into isolated Chromium browser contexts.
   - Executes in-page direct API `fetch()` via `page.evaluate()` concurrently with automated UI selector clicking (`asyncio.gather`).
   - Human-in-the-loop manual authentication protocol (`WAITING_LOGIN` state) in headful mode for Captchas and SSO portals.

5. **Built-in Platform Presets (`app/presets.py`)**:
   - **VAU Events / VAUVERSE 2026**: High-speed workshop & ticket registration.
   - **VAU Course Registration**: Academic portal credit enrollment with manual login support.

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.11)
- Playwright Chromium browser installed

### 2. Installation

Clone or navigate to the project directory:
```bash
cd auto_sniper_app
```

Install Python dependencies:
```bash
pip install -r requirements.txt
```

Install Playwright Chromium browser binaries:
```bash
playwright install chromium
```

### 3. Running the Server

Start the application with the default configuration (binds to `http://127.0.0.1:8000`):
```bash
python main.py
```

Or run via Uvicorn CLI directly:
```bash
uvicorn app.server:app --host 127.0.0.1 --port 8000 --reload
```

Access the web dashboard at:
👉 **`http://localhost:8000/`**

---

## 🔒 Security & Key Configuration

By default, an encryption key is generated automatically on first run and saved to `data/secret.key`.

To specify a custom master key, set the `SNIPER_MASTER_KEY` environment variable:
```bash
# Windows PowerShell
$env:SNIPER_MASTER_KEY = "YOUR_FERNET_URL_SAFE_BASE64_KEY"
python main.py

# Linux / macOS
export SNIPER_MASTER_KEY="YOUR_FERNET_URL_SAFE_BASE64_KEY"
python main.py
```

To generate a new compatible Fernet key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## 📡 REST API Reference

| Method | Endpoint | Description | Request Body | Response |
|---|---|---|---|---|
| `GET` | `/api/health` | System health check & active tasks count | None | `{"status": "ok", "active_tasks": 0}` |
| `GET` | `/api/presets` | List built-in platform presets | None | `list[PresetModel]` |
| `GET` | `/api/tasks` | List all tasks (passwords masked as `********`) | None | `list[TaskResponse]` |
| `POST` | `/api/tasks` | Create a new task definition | `TaskCreateRequest` | `201 Created`: `TaskResponse` |
| `GET` | `/api/tasks/{task_id}` | Retrieve single task configuration | None | `TaskResponse` |
| `PUT` | `/api/tasks/{task_id}` | Update existing task (rejects if active) | `TaskUpdateRequest` | `TaskResponse` |
| `DELETE` | `/api/tasks/{task_id}` | Stop execution and delete task | None | `{"status": "deleted"}` |
| `POST` | `/api/tasks/{task_id}/start` | Start sniper task in background | None | `{"status": "started"}` |
| `POST` | `/api/tasks/{task_id}/stop` | Stop active task worker | None | `{"status": "stopped"}` |
| `POST` | `/api/tasks/stop-all` | Emergency stop all active tasks | None | `{"status": "all_stopped"}` |
| `POST` | `/api/tasks/{task_id}/confirm_login` | Confirm manual login completion | None | `{"status": "login_confirmed"}` |

---

## 🔌 WebSocket & Streaming Reference

### WebSocket Channels

1. **Task Execution Log Stream**:
   - `ws://localhost:8000/ws/tasks/{task_id}/logs`
   - Immediately receives replayed past logs for this task, followed by real-time execution logs and status transitions.

2. **Global System Log Stream**:
   - `ws://localhost:8000/ws/logs`
   - Streams all log and status events across all registered tasks.

### Message Payload Formats

**Execution Log Message**:
```json
{
  "type": "log",
  "task_id": "c1f7a42e-8367-4d7a-8921-2a1e39b7bf5e",
  "timestamp": "12:15:30",
  "iso_time": "2026-10-10T12:15:30.123456+00:00",
  "level": "INFO",
  "message": "Trang báo hết chỗ. Đang chờ chu kỳ polling tiếp theo...",
  "metrics": {}
}
```

**Status Transition Event**:
```json
{
  "type": "status_change",
  "task_id": "c1f7a42e-8367-4d7a-8921-2a1e39b7bf5e",
  "status": "SUCCESS",
  "timestamp": "12:15:35",
  "iso_time": "2026-10-10T12:15:35.654321+00:00",
  "message": "Task status updated to SUCCESS"
}
```

### SSE Fallback Stream

- `GET /api/tasks/{task_id}/logs/stream`
- Content-Type: `text/event-stream`
- Replays past logs followed by live `data: { ... }\n\n` payloads and periodic `: ping\n\n` heartbeats.

---

## 🧪 Testing & Verification

Run the verification test suite:
```bash
pytest tests/ -v
```

Run mock registration portal integration tests:
```bash
pytest tests/test_mock_integration.py -v
```
