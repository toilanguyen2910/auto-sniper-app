"""Khởi chạy máy chủ thử nghiệm (Mock Portal) trên cổng 8081 để kiểm tra và thử nghiệm bot."""

import uvicorn
from tests.mock_target_server import app

if __name__ == "__main__":
    print("=" * 65)
    print("🚀 Đang khởi động Cổng Đăng ký Thử nghiệm (Mock Target Portal)...")
    print("👉 Địa chỉ cổng: http://127.0.0.1:8081")
    print("👉 Trang sự kiện mẫu: http://127.0.0.1:8081/events/cmuy26oks0o3c2mo4k39w31v5")
    print("👉 Trang học phần mẫu: http://127.0.0.1:8081/courses/registration")
    print("Nhấn Ctrl+C để dừng.")
    print("=" * 65)
    uvicorn.run(app, host="127.0.0.1", port=8081)
