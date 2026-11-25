#!/usr/bin/env python3
"""Quick CLI test for Decky Yomi OCR + segmentation + dictionary via engine_worker."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_DIR = "/home/deck/homebrew/settings/decky-yomi"
DEFAULT_IMAGE = os.path.join(DEFAULT_DATA_DIR, "capture.png")


def main():
    parser = argparse.ArgumentParser(description="Test Decky Yomi OCR pipeline on an image.")
    parser.add_argument("image", nargs="?", default=DEFAULT_IMAGE, help="Path to PNG image")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="Decky Yomi settings dir")
    parser.add_argument("--crop", help="Optional percent crop x,y,w,h (e.g. 15,70,70,30)")
    parser.add_argument("--engine", choices=["meikiocr", "manga_ocr"], default="meikiocr", help="OCR engine to run")
    args = parser.parse_args()

    image_path = args.image
    tmp_path = None
    if args.crop:
        x, y, w, h = [float(v) for v in args.crop.split(",")]
        sys.path.insert(0, ROOT)
        sys.path.insert(0, os.path.join(args.data_dir, "py_modules"))
        import shutil
        from main import Plugin

        fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="decky_yomi_ocr_test_")
        os.close(fd)
        shutil.copyfile(image_path, tmp_path)
        p = Plugin()
        p._crop_image(tmp_path, {"x": x, "y": y, "w": w, "h": h, "unit": "percent"})
        image_path = tmp_path

    env = dict(os.environ)
    env["HF_HOME"] = os.path.join(args.data_dir, "models")
    env["HF_HUB_OFFLINE"] = "1"

    reqs = (
        json.dumps({"method": "_init_engines", "args": []})
        + "\n"
        + json.dumps({"method": "run_ocr", "args": [image_path], "config": {"ocr_engine": args.engine, "ocr_preprocess": True}})
        + "\n"
    )
    t0 = time.monotonic()
    proc = subprocess.run(
        ["/usr/bin/python3", "-I", os.path.join(ROOT, "backend", "engine_worker.py"), ROOT, args.data_dir],
        input=reqs,
        text=True,
        capture_output=True,
        env=env,
        timeout=90,
    )
    elapsed = round(time.monotonic() - t0, 2)
    if tmp_path and os.path.exists(tmp_path):
        os.remove(tmp_path)

    print(f"Exit: {proc.returncode} ({elapsed}s)")
    for line in proc.stdout.strip().splitlines():
        print("OUT:", line)
    if proc.stderr.strip():
        print("ERR (tail):", "\n".join(proc.stderr.strip().splitlines()[-10:]))


if __name__ == "__main__":
    main()
