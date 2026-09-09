"""Run the local application. The server only listens on loopback."""
import argparse
import os
import threading
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
import uvicorn

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

if __name__ == "__main__":
    os.chdir(ROOT)
    parser = argparse.ArgumentParser(description="研念 / Yannian Workbench")
    parser.add_argument("--port", type=int, default=int(os.environ.get("YANNIAN_PORT", os.environ.get("YANJI_PORT", "8765"))))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not args.no_browser:
        threading.Timer(1.8, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, access_log=False)
