"""Data models and enums for the Sniper Engine."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskStatus(str, Enum):
    """Lifecycle states for sniper tasks."""

    PENDING = "PENDING"
    WAITING_LOGIN = "WAITING_LOGIN"
    POLLING = "POLLING"
    SNIPING = "SNIPING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class TaskCredentials(BaseModel):
    """Credentials for authentication and session recovery."""

    model_config = ConfigDict(extra="allow")

    username: Optional[str] = None
    password: Optional[str] = None
    totp_secret: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class TaskSelectors(BaseModel):
    """DOM selectors for authentication, slot detection, and UI triggers."""

    model_config = ConfigDict(extra="allow")

    # Authentication / Post-login detection
    post_login_url_pattern: Optional[str] = None
    post_login_selector: Optional[str] = None
    username_selector: Optional[str] = None
    password_selector: Optional[str] = None
    submit_selector: Optional[str] = None

    # Slot availability detection
    slot_selector: Optional[str] = None
    slot_keyword: Optional[str] = None
    full_keyword: Optional[str] = None
    target_course_codes: Optional[str] = None  # Comma-separated course codes, e.g. "IT001, MATH101"

    # Automated UI Trigger sequence
    ui_slot_selector: Optional[str] = None
    ui_submit_selector: Optional[str] = None
    ui_confirm_modal_selector: Optional[str] = None
    extra_selectors: Dict[str, str] = Field(default_factory=dict)


class ApiTriggerConfig(BaseModel):
    """Configuration for in-page direct API fetch registration."""

    model_config = ConfigDict(extra="allow")

    api_endpoint: Optional[str] = None
    api_method: str = "POST"
    api_headers: Dict[str, str] = Field(default_factory=dict)
    api_payload: Optional[Dict[str, Any]] = None
    success_status_codes: List[int] = Field(
        default_factory=lambda: [200, 201, 204]
    )
    success_keyword: Optional[str] = None


class TaskModel(BaseModel):
    """Core data model representing a sniper task."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Untitled Task"
    target_url: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: Optional[datetime] = None

    # Browser window & authentication options
    headless: bool = True
    manual_login: bool = False
    login_timeout_sec: int = 300

    # Polling configuration
    polling_interval: float = Field(default=3.5, ge=0.1, le=3600.0)
    jitter_sec: float = Field(default=0.5, ge=0.0, le=60.0)
    wait_condition: str = "domcontentloaded"  # domcontentloaded | networkidle | load
    block_media_assets: bool = True

    # Slot detection configuration
    detection_type: str = "DOM_SELECTOR"  # DOM_SELECTOR | KEYWORD | API_JSON
    detection_api_url: Optional[str] = None

    # Registration triggers
    enable_dual_trigger: bool = True
    credentials: TaskCredentials = Field(default_factory=TaskCredentials)
    selectors: TaskSelectors = Field(default_factory=TaskSelectors)
    api_trigger: ApiTriggerConfig = Field(default_factory=ApiTriggerConfig)

    @property
    def task_id(self) -> str:
        """Alias property for id."""
        return self.id

    @model_validator(mode="before")
    @classmethod
    def normalize_flat_fields(cls, data: Any) -> Any:
        """Allow initializing from flat JSON dictionaries by mapping into sub-models."""
        if not isinstance(data, dict):
            return data

        # Map 'task_id' to 'id' if 'id' is missing
        if "task_id" in data and "id" not in data:
            data["id"] = data["task_id"]

        # Ensure credentials dict
        cred_keys = {"username", "password", "totp_secret"}
        if any(k in data for k in cred_keys):
            creds = data.get("credentials")
            if not isinstance(creds, dict):
                creds = {}
            for k in cred_keys:
                if k in data and k not in creds:
                    creds[k] = data[k]
            data["credentials"] = creds

        # Ensure selectors dict
        selector_keys = {
            "post_login_url_pattern",
            "post_login_selector",
            "username_selector",
            "password_selector",
            "submit_selector",
            "slot_selector",
            "slot_keyword",
            "full_keyword",
            "ui_slot_selector",
            "ui_submit_selector",
            "ui_confirm_modal_selector",
        }
        if any(k in data for k in selector_keys):
            selectors = data.get("selectors")
            if not isinstance(selectors, dict):
                selectors = {}
            for k in selector_keys:
                if k in data and k not in selectors:
                    selectors[k] = data[k]
            data["selectors"] = selectors

        # Ensure api_trigger dict
        api_keys = {
            "api_endpoint",
            "api_method",
            "api_headers",
            "api_payload",
            "success_status_codes",
            "success_keyword",
        }
        if any(k in data for k in api_keys):
            api_cfg = data.get("api_trigger")
            if not isinstance(api_cfg, dict):
                api_cfg = {}
            for k in api_keys:
                if k in data and k not in api_cfg:
                    api_cfg[k] = data[k]
            data["api_trigger"] = api_cfg

        return data


class LogEntry(BaseModel):
    """Structured log record emitted during task execution."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    level: str = "INFO"  # INFO | WARNING | ERROR | SUCCESS | CRITICAL
    message: str
    metrics: Optional[Dict[str, Any]] = None
