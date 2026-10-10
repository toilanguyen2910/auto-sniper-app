"""
Tier 1: Feature Coverage Test Suite
Tests fundamental behaviors and contracts of the mock target server,
BrowserManager context isolation, and DualTriggerExecutor operations.
"""

import asyncio
from functools import wraps
import pytest
import httpx

from tests.mock_target_server import start_mock_server, mock_state
from sniper.models import TaskModel, TaskStatus, ApiTriggerConfig, TaskSelectors
from sniper.browser_manager import BrowserManager
from sniper.dual_trigger import DualTriggerExecutor


def async_test(f):
    """Decorator to run async test functions synchronously via asyncio.run."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


@pytest.fixture(scope="module")
def mock_server_tier1():
    """Start mock target server on port 8082 for Tier 1 tests."""
    handle = start_mock_server(host="127.0.0.1", port=8082)
    yield "http://127.0.0.1:8082"
    handle.stop()


@async_test
async def test_mock_server_health_and_initial_full_state(mock_server_tier1):
    """Verify mock server health endpoint and initial 50/50 full capacity."""
    base_url = mock_server_tier1
    async with httpx.AsyncClient(base_url=base_url) as client:
        # Reset to ensure clean initial state
        await client.post("/mock/reset")

        # Health check
        res = await client.get("/mock/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["service"] == "mock_target_server"

        # Status check
        status_res = await client.get("/mock/status")
        assert status_res.status_code == 200
        status = status_res.json()
        assert status["total_slots"] == 50
        assert status["occupied_slots"] == 50
        assert status["available_slots"] == 0
        assert status["is_full"] is True
        assert status["registered_participants_count"] == 0

        # HTML Portal check
        html_res = await client.get("/")
        assert html_res.status_code == 200
        html = html_res.text
        assert "Trạng thái: Đã hết chỗ (50/50)" in html
        assert "Đã đạt số lượng tối đa" in html
        assert 'id="btn-register" disabled' in html
        assert "Hết chỗ" in html


@async_test
async def test_mock_server_slot_release_and_registration_lifecycle(mock_server_tier1):
    """Verify releasing a slot dynamically enables registration and transitions back to full."""
    base_url = mock_server_tier1
    async with httpx.AsyncClient(base_url=base_url) as client:
        await client.post("/mock/reset")

        # 1. Attempt registration while full -> Expect 409
        reg_fail = await client.post(
            "/api/events/mock-event-vau/register",
            json={"sessionIds": ["slot-alpha"]},
        )
        assert reg_fail.status_code == 409
        assert reg_fail.json()["detail"]["success"] is False

        # 2. Trigger slot release
        release_res = await client.post("/mock/release-slot")
        assert release_res.status_code == 200
        rel_data = release_res.json()
        assert rel_data["occupied_slots"] == 49
        assert rel_data["is_full"] is False
        assert rel_data["available_slots"] == 1

        # 3. Check HTML portal now reflects availability
        html_res = await client.get("/events/mock-event-vau")
        assert "Trạng thái: Còn chỗ trống (49/50)" in html_res.text
        assert "Đăng ký tham gia" in html_res.text
        assert "btn-disabled" not in html_res.text

        # 4. Perform successful registration
        reg_ok = await client.post(
            "/api/events/mock-event-vau/register",
            json={"sessionIds": ["slot-alpha"]},
        )
        assert reg_ok.status_code == 200
        data = reg_ok.json()
        assert data["success"] is True
        assert "reservation_id" in data
        assert data["occupied_slots"] == 50

        # 5. Verify server is full again
        status = (await client.get("/mock/status")).json()
        assert status["is_full"] is True
        assert status["occupied_slots"] == 50
        assert status["registered_participants_count"] == 1


@async_test
async def test_mock_server_reset_endpoint(mock_server_tier1):
    """Verify /mock/reset restores 50/50 capacity and clears participant log."""
    base_url = mock_server_tier1
    async with httpx.AsyncClient(base_url=base_url) as client:
        await client.post("/mock/release-slot")
        await client.post(
            "/api/events/mock-event-vau/register",
            json={"sessionIds": ["slot-test"]},
        )

        reset_res = await client.post("/mock/reset")
        assert reset_res.status_code == 200

        status = (await client.get("/mock/status")).json()
        assert status["total_slots"] == 50
        assert status["occupied_slots"] == 50
        assert status["is_full"] is True
        assert status["registered_participants_count"] == 0


@async_test
async def test_browser_manager_context_lifecycle_and_isolation():
    """Verify BrowserManager creates isolated contexts with strict cookie/session boundary."""
    bm = BrowserManager()
    try:
        # Create Context 1
        ctx1 = await bm.create_context(task_id="task-ctx-1", headless=True)
        assert ctx1 is not None

        # Create Context 2
        ctx2 = await bm.create_context(task_id="task-ctx-2", headless=True)
        assert ctx2 is not None

        # Add cookie in Context 1
        await ctx1.add_cookies([
            {"name": "session_token", "value": "secret_task_1", "domain": "localhost", "path": "/"}
        ])

        cookies1 = await ctx1.cookies("http://localhost")
        cookies2 = await ctx2.cookies("http://localhost")

        # Context 1 must have the cookie, Context 2 must NOT
        assert any(c["name"] == "session_token" for c in cookies1)
        assert not any(c["name"] == "session_token" for c in cookies2)

        # Close Context 1
        await bm.close_context("task-ctx-1")
        assert bm.get_context("task-ctx-1") is None
        assert bm.get_context("task-ctx-2") is not None
    finally:
        await bm.shutdown()


@async_test
async def test_dual_trigger_api_execution_against_mock(mock_server_tier1):
    """Verify DualTriggerExecutor executes in-page direct API fetch against mock server."""
    base_url = mock_server_tier1
    async with httpx.AsyncClient(base_url=base_url) as client:
        await client.post("/mock/reset")
        await client.post("/mock/release-slot")  # Free 1 slot

    bm = BrowserManager()
    try:
        ctx = await bm.create_context(task_id="test-api-trigger", headless=True)
        page = await ctx.new_page()
        await page.goto(f"{base_url}/events/mock-event-vau", wait_until="domcontentloaded")

        task = TaskModel(
            id="test-api-task",
            target_url=f"{base_url}/events/mock-event-vau",
            enable_dual_trigger=True,
            api_trigger=ApiTriggerConfig(
                api_endpoint=f"{base_url}/api/events/mock-event-vau/register",
                api_method="POST",
                api_payload={"sessionIds": ["slot-alpha"]},
                success_status_codes=[200],
            ),
        )

        result = await DualTriggerExecutor.execute(page, task)
        assert result.success is True
        assert result.winner in ("API", "BOTH")
        assert result.api_result.get("ok") is True
        assert result.api_result.get("status") == 200

        # Verify on mock server
        async with httpx.AsyncClient(base_url=base_url) as client:
            status = (await client.get("/mock/status")).json()
            assert status["occupied_slots"] == 50
            assert status["is_full"] is True
            assert status["registered_participants_count"] >= 1
    finally:
        await bm.shutdown()


@async_test
async def test_dual_trigger_ui_execution_against_mock(mock_server_tier1):
    """Verify DualTriggerExecutor executes UI selector clicking against mock server."""
    base_url = mock_server_tier1
    async with httpx.AsyncClient(base_url=base_url) as client:
        await client.post("/mock/reset")
        await client.post("/mock/release-slot")  # Free 1 slot

    bm = BrowserManager()
    try:
        ctx = await bm.create_context(task_id="test-ui-trigger", headless=True)
        page = await ctx.new_page()
        await page.goto(f"{base_url}/events/mock-event-vau", wait_until="domcontentloaded")

        task = TaskModel(
            id="test-ui-task",
            target_url=f"{base_url}/events/mock-event-vau",
            enable_dual_trigger=False,  # UI trigger only
            selectors=TaskSelectors(
                ui_slot_selector="#btn-register",
                ui_submit_selector="#btn-confirm-register",
            ),
        )

        result = await DualTriggerExecutor.execute(page, task)
        assert result.success is True
        assert result.winner == "UI"
        assert result.ui_result.get("ok") is True

        # Wait a moment for UI fetch to finish
        await asyncio.sleep(0.5)

        # Verify on mock server
        async with httpx.AsyncClient(base_url=base_url) as client:
            status = (await client.get("/mock/status")).json()
            assert status["occupied_slots"] == 50
            assert status["is_full"] is True
            assert status["successful_method"] in ("ui", "api", "dual")
    finally:
        await bm.shutdown()


@async_test
async def test_dual_trigger_rejected_when_portal_is_full(mock_server_tier1):
    """Verify DualTriggerExecutor handles 409 rejection gracefully when portal is full."""
    base_url = mock_server_tier1
    async with httpx.AsyncClient(base_url=base_url) as client:
        await client.post("/mock/reset")  # 50/50 Full

    bm = BrowserManager()
    try:
        ctx = await bm.create_context(task_id="test-full-rejection", headless=True)
        page = await ctx.new_page()
        await page.goto(f"{base_url}/events/mock-event-vau", wait_until="domcontentloaded")

        task = TaskModel(
            id="test-rejected-task",
            target_url=f"{base_url}/events/mock-event-vau",
            enable_dual_trigger=True,
            api_trigger=ApiTriggerConfig(
                api_endpoint=f"{base_url}/api/events/mock-event-vau/register",
                api_method="POST",
                api_payload={"sessionIds": ["slot-alpha"]},
                success_status_codes=[200],
            ),
        )

        result = await DualTriggerExecutor.execute(page, task)
        assert result.success is False
        assert result.api_result.get("ok") is False
        assert result.api_result.get("status") == 409
    finally:
        await bm.shutdown()
