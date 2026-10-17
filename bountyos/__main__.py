"""Run the local dashboard and its background workers."""

import argparse
import json
import os
import threading
import time
from urllib import error, request
import webbrowser

import uvicorn


def open_dashboard_when_ready(port: int) -> None:
    """Open the local UI only after the app answers its health endpoint."""
    opener = request.build_opener(request.ProxyHandler({}))
    address = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            with opener.open(address + "/api/health", timeout=1) as response:
                health = json.loads(response.read(4096))
            if health.get("ok") is True and health.get("service") == "BountyOS":
                webbrowser.open(address)
                return
        except (error.URLError, TimeoutError, OSError, ValueError):
            pass
        time.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="BountyOS local dashboard")
    parser.add_argument("--open-browser", action="store_true", help="Open the dashboard when the server is ready")
    args = parser.parse_args()
    port = int(os.environ.get("BOUNTYOS_PORT", "8787"))
    if not 1 <= port <= 65535:
        raise SystemExit("BOUNTYOS_PORT must be between 1 and 65535")
    if args.open_browser:
        threading.Thread(target=open_dashboard_when_ready, args=(port,), daemon=True).start()
    uvicorn.run("bountyos.app:app", host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()

