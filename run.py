import os
import time
import threading
import webbrowser

import uvicorn


def open_browser():
    # Local development convenience only. Cloud Run will not use this path.
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:8000")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))

    print("=" * 50)
    print("Starting DataPilot...")
    print("=" * 50)
    print(f"Open: http://127.0.0.1:{port}")
    print("Press Ctrl+C to stop.")
    print("=" * 50)

    # Browser auto-open is useful locally but is skipped in explicit production mode.
    if os.getenv("APP_ENV", "development").lower() != "production":
        threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
