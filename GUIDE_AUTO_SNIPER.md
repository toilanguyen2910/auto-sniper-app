# 🚀 CẨM NANG & PROMPT MẪU: CANH CHỖ / ĐĂNG KÝ TỰ ĐỘNG SIÊU TỐC (WEB AUTO-SNIPER)

Tài liệu này lưu lại toàn bộ **Prompt mẫu chuẩn** và **Mô hình kiến trúc Bot (Playwright + Direct API)** được đúc kết từ thực chiến thành công trên hệ thống đại học (Next.js, SSO Keycloak, Cổng đào tạo).

---

## 📌 PHẦN 1: PROMPT MẪU ĐỂ YÊU CẦU AI VIẾT BOT (COPY DÙNG NGAY)

> *Khi gặp một trang web mới cần canh slot (sự kiện, vé, tín chỉ, lịch hẹn), bạn chỉ cần copy đoạn Prompt dưới đây và gửi cho AI:*

```markdown
Tôi cần bạn viết một bot Python tự động canh và đăng ký slot (sự kiện / môn học) ngay khi có người hủy hoặc vừa mở cổng.

Thông tin trang web:
- URL mục tiêu: <DÁN LINK CỦA BẠN VÀO ĐÂY>
- Tài khoản: <TÊN ĐĂNG NHẬP>
- Mật khẩu: <MẬT KHẨU>
- Đối tượng cần đăng ký: <TÊN MÔN / BUỔI / VÉ CỤ THỂ>

Yêu cầu kỹ thuật bắt buộc:
1. Sử dụng Playwright (Python async) với trình duyệt Chromium thật (headless=False) để xử lý hoàn hảo cookie, phiên đăng nhập SSO hoặc Captcha.
2. Không chỉ click giao diện đơn thuần, hãy phân tích mã nguồn trang (hoặc các bundle JS nếu là Next.js/SPA) để tìm API nội bộ gửi request đăng ký (thường là POST đến endpoint dạng /api/.../register).
3. Triển khai cơ chế KÍCH HOẠT KÉP (Dual Trigger):
   - Ngay khi phát hiện có chỗ trống, chạy song song (asyncio.gather):
     + Cách 1: Gọi trực tiếp API bằng page.evaluate(fetch(...)) với đúng cấu trúc JSON payload và credentials: 'include'. Tốc độ dưới 50ms.
     + Cách 2: Tự động click UI: Bấm nút mở đăng ký -> Tick chính xác checkbox buổi/môn học (dùng đúng id/selector) -> Bấm nút 'Xác nhận'.
4. Tần suất kiểm tra: Cứ mỗi 3-5 giây reload kiểm tra trạng thái một lần (dùng domcontentloaded để tải nhanh).
5. Khi đăng ký thành công, phát chuông báo động (print('\a')) và giữ cửa sổ trình duyệt mở để tôi kiểm tra lại.
```

---

## 🛠️ PHẦN 2: BỘ KHUNG CODE CHUẨN (PYTHON PLAYWRIGHT BLUEPRINT)

```python
import asyncio
import sys
import time
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# 1. Cấu hình
USERNAME = "YOUR_USERNAME"
PASSWORD = "YOUR_PASSWORD"
TARGET_URL = "https://example.com/events/123"
CHECK_INTERVAL = 5  # giây


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO": "ℹ️", "OK": "✅", "WARN": "⚡", "SUCCESS": "🎉"}
    print(f"[{ts}] {icons.get(level, '·')} {msg}", flush=True)


async def fast_register(page):
    log("⚡ Kích hoạt đăng ký kép siêu tốc...", "WARN")

    # [Kênh 1]: Bắn API nội bộ trực tiếp bằng cookie session
    api_task = page.evaluate("""
        async () => {
            try {
                const res = await fetch('/api/events/123/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sessionIds: ['SESSION_ID_HERE'] }),
                    credentials: 'include'
                });
                return { status: res.status, text: await res.text() };
            } catch (err) {
                return { error: err.message };
            }
        }
    """)

    # [Kênh 2]: Tự động click giao diện song song
    async def ui_task():
        try:
            # 1. Bấm nút đăng ký
            btn = page.locator('button:has-text("Đăng ký"), button:has-text("Tham gia")').first
            await btn.click(timeout=1500)

            # 2. Tick chọn checkbox buổi/môn học
            cb = page.locator('input[type="checkbox"], [role="checkbox"]').first
            await cb.wait_for(state="visible", timeout=2000)
            await cb.click(timeout=1000)

            # 3. Bấm xác nhận
            confirm = page.locator('button:has-text("Xác nhận"), button:has-text("Lưu")').first
            await confirm.click(timeout=1500)
        except Exception:
            pass

    # Chạy song song cả hai kênh
    api_res, _ = await asyncio.gather(api_task, ui_task(), return_exceptions=True)
    return isinstance(api_res, dict) and api_res.get("status") in (200, 201)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=50)
        page = await browser.new_page()

        # Bước đăng nhập
        await page.goto("https://example.com/login", wait_until="networkidle")
        await page.fill('input[name="username"]', USERNAME)
        await page.fill('input[name="password"]', PASSWORD)
        await page.click('button[type="submit"]')
        await page.wait_for_url("https://example.com/**")

        # Chuyển tới trang cần canh
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        log("Bắt đầu canh chỗ...")

        while True:
            await page.reload(wait_until="domcontentloaded")
            content = await page.content()

            # Nhận diện slot mở
            is_full = "đã đầy" in content.lower() or "hết chỗ" in content.lower()
            if not is_full:
                log("🔔 PHÁT HIỆN CÓ CHỖ TRỐNG!", "SUCCESS")
                if await fast_register(page):
                    log("🎉 ĐĂNG KÝ THÀNH CÔNG!", "SUCCESS")
                    for _ in range(6):
                        print("\a", end="", flush=True)
                        await asyncio.sleep(0.3)
                    await asyncio.sleep(120)
                    break

            await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🧠 BÀI HỌC KINH NGHIỆM THỰC CHIẾN

| Tình huống thực tế | Cách xử lý tối ưu |
| :--- | :--- |
| **SSO chặn Direct API (OAuth error 401/400)** | Bỏ qua việc gọi token API bên ngoài, dùng Playwright mở Chrome thật để server tự cấp Cookie phiên đăng nhập. |
| **Bấm nút Đăng ký xong bị mất slot** | Lỗi do popup bắt chọn Buổi/Học phần nhưng script không tick checkbox. Luôn kiểm tra selector của checkbox (`#session-pick-...`). |
| **Bị người khác cướp trước 1 tích tắc** | Bỏ hoàn toàn `sleep()` thừa. Dùng `page.evaluate()` bắn API `fetch()` trực tiếp từ trình duyệt trong **0.05 giây**. |
