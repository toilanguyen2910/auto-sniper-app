"""Web Auto-Sniper Application Entry Point."""

import argparse
import sys
import uvicorn


def main():
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
    args = parser.parse_args()

    print(f"[*] Starting Auto-Sniper App on http://{args.host}:{args.port}")
    uvicorn.run(
        "app.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
