#!/usr/bin/env python3
"""Automated Gaming Mode E2E test & data-point collector for Decky Yomi.

Runs in Gaming Mode (or Desktop Mode with --no-wait-gamescope), optionally launches a
Steam game, captures screenshots, runs OCR + segmentation + dictionary lookup, and
writes full artifacts (PNGs + JSON report) into test_runs/<timestamp>/.
"""
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_DIR = "/home/deck/homebrew/settings/decky-yomi"


def is_gamescope_running() -> bool:
    res = subprocess.run(["pgrep", "-x", "gamescope"], capture_output=True)
    return res.returncode == 0


async def run_e2e(args):
    if not args.no_wait_gamescope:
        print("Waiting for gamescope session...")
        deadline = time.monotonic() + args.timeout
        while not is_gamescope_running():
            if time.monotonic() > deadline:
                raise TimeoutError("Timed out waiting for gamescope")
            await asyncio.sleep(1.0)
        print("Gamescope detected.")

    if args.appid:
        print(f"Launching Steam app {args.appid}...")
        subprocess.Popen(["steam", "-applaunch", str(args.appid)])
        await asyncio.sleep(args.launch_wait)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(ROOT, "test_runs", ts)
    os.makedirs(out_dir, exist_ok=True)

    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(args.data_dir, "py_modules"))
    from main import Plugin

    plugin = Plugin()
    plugin.data_dir = args.data_dir
    plugin.config_path = os.path.join(args.data_dir, "config.json")
    plugin._load_config()

    # 1. Fullscreen capture for reference
    full_path = os.path.join(out_dir, "fullscreen.png")
    cap_path = await plugin.capture_screen(region=None if args.fullscreen else plugin.get_configured_region())
    if isinstance(cap_path, str) and not cap_path.startswith("ERROR") and os.path.exists(cap_path):
        shutil.copyfile(cap_path, full_path)

    # 2. Run OCR + analysis via engine_worker (with offline HF cache)
    env = dict(os.environ)
    env["HF_HOME"] = os.path.join(args.data_dir, "models")
    env["HF_HUB_OFFLINE"] = "1"
    reqs = (
        json.dumps({"method": "_init_engines", "args": []})
        + "\n"
        + json.dumps({"method": "run_ocr", "args": [full_path], "config": plugin.config})
        + "\n"
    )
    t0 = time.monotonic()
    proc = subprocess.run(
        ["/usr/bin/python3", "-I", os.path.join(ROOT, "backend", "engine_worker.py"), ROOT, args.data_dir],
        input=reqs,
        text=True,
        capture_output=True,
        env=env,
        timeout=120,
    )
    elapsed_s = round(time.monotonic() - t0, 2)

    lines = [json.loads(l) for l in proc.stdout.strip().splitlines() if l.strip()]
    ocr_text = lines[-1].get("result", "") if lines else ""

    report = {
        "timestamp": ts,
        "gamescope": is_gamescope_running(),
        "appid": args.appid,
        "capture_result": cap_path,
        "saved_image": full_path if os.path.exists(full_path) else None,
        "ocr_text": ocr_text,
        "elapsed_s": elapsed_s,
        "worker_rc": proc.returncode,
        "region_config": plugin.get_configured_region(),
    }
    report_path = os.path.join(out_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Saved E2E report to {report_path}")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Decky Yomi Gaming Mode E2E collector")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--appid", type=int, help="Optional Steam AppID to launch before capture")
    parser.add_argument("--launch-wait", type=float, default=15.0, help="Seconds to wait after launching app")
    parser.add_argument("--timeout", type=float, default=120.0, help="Seconds to wait for gamescope")
    parser.add_argument("--no-wait-gamescope", action="store_true", help="Run immediately without waiting for gamescope")
    parser.add_argument("--fullscreen", action="store_true", help="Capture fullscreen instead of configured region")
    args = parser.parse_args()
    asyncio.run(run_e2e(args))


if __name__ == "__main__":
    main()
