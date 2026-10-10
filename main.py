"""Web Auto-Sniper Application Entry Point."""

import argparse
import sys
import uvicorn


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Web Auto-Sniper Application Server")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Enable auto-reload for development",
    )
    parser.add_argument(
        "--with-mock",
        action="store_true",
        default=False,
        help="Khởi chạy đồng thời máy chủ thử nghiệm cục bộ trên cổng 8081",
    )
    args = parser.parse_args()

    if args.with_mock:
        import multiprocessing
        def _run_mock():
            from tests.mock_target_server import app as mock_app
            uvicorn.run(mock_app, host="127.0.0.1", port=8081, log_level="warning")
        p = multiprocessing.Process(target=_run_mock, daemon=True)
        p.start()
        print("[*] Đã khởi chạy máy chủ thử nghiệm tại http://127.0.0.1:8081")

    print(f"[*] Starting Auto-Sniper App on http://{args.host}:{args.port}")
    uvicorn.run(
        "app.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
