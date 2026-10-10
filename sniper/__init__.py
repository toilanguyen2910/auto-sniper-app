"""Sniper Package - Async Multi-Task Sniper Engine for Web Registrations."""

from sniper.browser_manager import BrowserManager
from sniper.dual_trigger import (
    DualTriggerExecutor,
    DualTriggerResult,
    execute_dual_trigger,
)
from sniper.engine import (
    AwaitableBool,
    AwaitableNone,
    SniperEngine,
    SniperError,
    TaskAlreadyRunningError,
    TaskNotFoundError,
)
from sniper.models import (
    ApiTriggerConfig,
    LogEntry,
    TaskCredentials,
    TaskModel,
    TaskSelectors,
    TaskStatus,
)

__all__ = [
    "TaskStatus",
    "TaskCredentials",
    "TaskSelectors",
    "ApiTriggerConfig",
    "TaskModel",
    "LogEntry",
    "BrowserManager",
    "DualTriggerExecutor",
    "DualTriggerResult",
    "execute_dual_trigger",
    "SniperEngine",
    "SniperError",
    "TaskNotFoundError",
    "TaskAlreadyRunningError",
    "AwaitableBool",
    "AwaitableNone",
]
