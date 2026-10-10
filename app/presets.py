"""Pre-configured platform presets for high-speed sniper tasks."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class PresetModel(BaseModel):
    """Data model describing a sniper configuration preset."""

    id: str
    name: str
    description: str
    preset_type: str
    target_url: str
    polling_interval: float = 3.5
    jitter_sec: float = 0.5
    wait_condition: str = "domcontentloaded"
    block_media_assets: bool = True
    headless: bool = True
    manual_login: bool = False
    login_timeout_sec: int = 300
    detection_type: str = "DOM_SELECTOR"
    enable_dual_trigger: bool = True
    selectors: Dict[str, Any] = Field(default_factory=dict)
    api_trigger: Dict[str, Any] = Field(default_factory=dict)


VAU_EVENTS_PRESET = PresetModel(
    id="vau_events",
    name="VAU Events / VAUVERSE 2026",
    description="Canh và đăng ký vé sự kiện trường VAU (VAUVERSE / TechFest / Workshop) siêu tốc với cơ chế kích hoạt kép (Dual-Trigger API & UI).",
    preset_type="vau_events",
    target_url="http://127.0.0.1:8081/events/cmuy26oks0o3c2mo4k39w31v5",
    polling_interval=3.5,
    jitter_sec=0.5,
    wait_condition="domcontentloaded",
    block_media_assets=True,
    headless=True,
    manual_login=False,
    login_timeout_sec=300,
    detection_type="DOM_SELECTOR",
    enable_dual_trigger=True,
    selectors={
        "slot_selector": "#btn-register",
        "slot_keyword": "Đăng ký tham gia",
        "full_keyword": "Hết chỗ",
        "ui_slot_selector": "#btn-register",
        "ui_submit_selector": "#btn-confirm-register",
        "ui_confirm_modal_selector": "#register-modal",
    },
    api_trigger={
        "api_endpoint": "/api/events/cmuy26oks0o3c2mo4k39w31v5/register",
        "api_method": "POST",
        "api_headers": {"Content-Type": "application/json"},
        "api_payload": {"sessionIds": ["cmuy26okv0o3d2mo4fyiepdc1"]},
        "success_status_codes": [200, 201],
        "success_keyword": "Đăng ký thành công",
    },
)

VAU_COURSE_PRESET = PresetModel(
    id="vau_course",
    name="Cổng VAU - Đăng ký học phần (sv.vau.edu.vn)",
    description="Hỗ trợ tự động điền tài khoản, chờ nhập mã Captcha và tự động quét/tick môn học theo mã lớp trên sv.vau.edu.vn.",
    preset_type="vau_course",
    target_url="https://sv.vau.edu.vn/dang-ky-hoc-phan.html",
    polling_interval=3.0,
    jitter_sec=0.5,
    wait_condition="domcontentloaded",
    block_media_assets=False,
    headless=False,
    manual_login=True,
    login_timeout_sec=300,
    detection_type="DOM_SELECTOR",
    enable_dual_trigger=False,
    selectors={
        "username_selector": "#UserName, input[name='UserName']",
        "password_selector": "#Password, input[name='Password']",
        "submit_selector": "input[type='submit'], .button",
        "post_login_url_pattern": ".*/dang-ky-hoc-phan.*",
        "slot_selector": "input[type='checkbox']",
        "slot_keyword": "Đăng ký",
        "full_keyword": "Hết chỗ",
        "ui_slot_selector": "input[type='checkbox']",
        "ui_submit_selector": "#btnLuu, #btnDangKy, input[value='Lưu đăng ký'], input[value='Đăng ký']",
        "target_course_codes": "",
    },
    api_trigger={},
)

AVAILABLE_PRESETS: Dict[str, PresetModel] = {
    VAU_EVENTS_PRESET.id: VAU_EVENTS_PRESET,
    VAU_COURSE_PRESET.id: VAU_COURSE_PRESET,
}


def get_all_presets() -> List[PresetModel]:
    """Return all available preset definitions."""
    return list(AVAILABLE_PRESETS.values())


def get_preset_by_id(preset_id: str) -> Optional[PresetModel]:
    """Retrieve preset by ID if present."""
    return AVAILABLE_PRESETS.get(preset_id)
