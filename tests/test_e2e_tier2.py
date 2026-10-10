"""
Tier 2: Boundary & Corner Cases Test Suite
Tests boundary conditions, concurrency race conditions on scarce slots,
rapid state transitions, error handling on invalid inputs, and task lifecycle boundaries.
"""

import asyncio
from functools import wraps
import pytest
import httpx

from tests.mock_target_server import start_mock_server, mock_state
from sniper.engine import (
    SniperEngine,
    TaskNotFoundError,
    TaskAlreadyRunningError,
)
from sniper.models import (
    ApiTriggerConfig,
    TaskModel,
    TaskSelectors,
    TaskStatus,
)


def async_test(f):
    """Decorator to run async test functions synchronously via asyncio.run."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


@pytest.fixture(scope="module")
def mock_server_tier2():
    """Start mock target server on port 8083 for Tier 2 tests."""
    handle = start_mock_server(host="127.0.0.1", port=8083)
    yield "http://127.0.0.1:8083"
    handle.stop()


@async_test
async def test_boundary_single_slot_concurrency_race(mock_server_tier2):
    """
    Test extreme concurrency race condition:
    When exactly 1 slot is freed (49/50), 5 simultaneous registration requests compete.
    Exactly 1 request MUST succeed (HTTP 200), and all 4 others MUST be rejected (HTTP 409).
    """
    base_url = mock_server_tier2
    async with httpx.AsyncClient(base_url=base_url) as client:
        # Reset and free exactly 1 slot
        await client.post("/mock/reset")
        await client.post("/mock/release-slot")

        status_before = (await client.get("/mock/status")).json()
        assert status_before["occupied_slots"] == 49
        assert status_before["is_full"] is False

        # Fire 5 concurrent registration requests
        async def make_reg_request(user_id: int):
            return await client.post(
                "/api/events/mock-event-vau/register",
                json={"sessionIds": [f"slot-contention-{user_id}"]},
            )

        responses = await asyncio.gather(*[make_reg_request(i) for i in range(5)])

        successes = [r for r in responses if r.status_code == 200]
        failures = [r for r in responses if r.status_code == 409]

        assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
        assert len(failures) == 4, f"Expected exactly 4 failures, got {len(failures)}"

        # Server must be exactly 50/50 full now
        status_after = (await client.get("/mock/status")).json()
        assert status_after["occupied_slots"] == 50
        assert status_after["is_full"] is True
        assert status_after["registered_participants_count"] == 1


@async_test
async def test_boundary_invalid_and_malformed_inputs(mock_server_tier2):
    """Verify robust error responses for malformed JSON, invalid endpoints, and bad inputs."""
    base_url = mock_server_tier2
    async with httpx.AsyncClient(base_url=base_url) as client:
        # 1. Non-existent path returns 404
        r404 = await client.get("/api/non-existent-endpoint")
        assert r404.status_code == 404

        # 2. Missing body on POST register returns 422 Unprocessable Entity
        r422 = await client.post(
            "/api/events/mock-event-vau/register",
            content=b"",
            headers={"Content-Type": "application/json"},
        )
        assert r422.status_code == 422

        # 3. Malformed JSON returns 422
        rmalformed = await client.post(
            "/api/events/mock-event-vau/register",
            content=b"{invalid_json:",
            headers={"Content-Type": "application/json"},
        )
        assert rmalformed.status_code == 422


@async_test
async def test_boundary_rapid_state_saturation_cycle(mock_server_tier2):
    """Verify thread-safe state under rapid repeated cycles of release, register, and reset."""
    base_url = mock_server_tier2
    async with httpx.AsyncClient(base_url=base_url) as client:
        for cycle in range(5):
            # Reset
            await client.post("/mock/reset")
            # Free slot
            rel = await client.post("/mock/release-slot")
            assert rel.json()["occupied_slots"] == 49
            # Claim slot
            reg = await client.post(
                "/api/events/mock-event-vau/register",
                json={"sessionIds": [f"rapid-cycle-{cycle}"]},
            )
            assert reg.status_code == 200
            # Must now be full
            status = (await client.get("/mock/status")).json()
            assert status["occupied_slots"] == 50
            assert status["is_full"] is True


@async_test
async def test_boundary_task_not_found_and_duplicate_exceptions():
    """Verify SniperEngine raises explicit typed exceptions for invalid operations."""
    engine = SniperEngine()
    try:
        # 1. Stop non-existent task
        with pytest.raises(TaskNotFoundError):
            engine.stop_task("non-existent-task-id-12345")

        # 2. Get status of non-existent task
        with pytest.raises(TaskNotFoundError):
            engine.get_task_status("non-existent-task-id-12345")

        # 3. Starting an already running task raises TaskAlreadyRunningError
        task = TaskModel(
            id="running-task-boundary",
            target_url="http://127.0.0.1:8083/",
            polling_interval=10.0,
            headless=True,
        )
        await engine.start_task(task)

        with pytest.raises(TaskAlreadyRunningError):
            await engine.start_task(task)

        # Clean stop
        engine.stop_task(task.id)
    finally:
        await engine.browser_manager.shutdown()


@async_test
async def test_boundary_rapid_stop_during_initialization(mock_server_tier2):
    """Verify stopping a task immediately after launch cleanly halts without leaks."""
    base_url = mock_server_tier2
    engine = SniperEngine()
    try:
        task = TaskModel(
            id="rapid-stop-task",
            target_url=f"{base_url}/events/mock-event-vau",
            polling_interval=2.0,
            headless=True,
        )
        await engine.start_task(task)

        # Stop immediately
        engine.stop_task(task.id)
        await asyncio.sleep(0.5)

        status = engine.get_task_status(task.id)
        assert status == TaskStatus.STOPPED
    finally:
        await engine.browser_manager.shutdown()
