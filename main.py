import os
import sys
import json
import asyncio
import subprocess
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
import functools
import time
import traceback
import uuid
from pathlib import Path
from datetime import datetime
import shutil
import platform
import urllib.request
import hashlib
import zipfile

# Add plugin directory and vendored modules to path before importing submodules
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)
BACKEND_DIR = os.path.join(PLUGIN_DIR, "backend")
if os.path.exists(BACKEND_DIR) and BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
PY_MODULES = os.path.join(PLUGIN_DIR, "py_modules")
if os.path.exists(PY_MODULES) and PY_MODULES not in sys.path:
    sys.path.insert(0, PY_MODULES)

from backend.japanese import JapaneseLookup, custom_dictionaries

try:
    from backend.controller_monitor import ControllerButtonMonitor
except ImportError:
    try:
        from controller_monitor import ControllerButtonMonitor
    except ImportError:
        ControllerButtonMonitor = None

try:
    import decky  # type: ignore
except ImportError:
    # Standalone / testing fallback when outside of Decky Loader environment
    class MockDeckyLogger:
        @staticmethod
        def info(msg): print(f"[DeckyYomi:INFO] {msg}")
        @staticmethod
        def warning(msg): print(f"[DeckyYomi:WARN] {msg}")
        @staticmethod
        def error(msg): print(f"[DeckyYomi:ERR] {msg}")

    class MockDecky:
        DECKY_PLUGIN_SETTINGS_DIR = os.path.join(PLUGIN_DIR, ".test_data")
        DECKY_PLUGIN_DIR = PLUGIN_DIR
        logger = MockDeckyLogger()

    decky = MockDecky()  # type: ignore

# ─── Lazy-loaded heavy modules ─────────────────────────────────
manga_ocr_engine = None
meiki_ocr_engine = None
morphological_tagger = None
dictionary_engine = None

GET_PIP_URL = "https://raw.githubusercontent.com/pypa/get-pip/8c427468071b5d15717839ffd76c53a96df8b703/public/get-pip.py"
GET_PIP_SHA256 = "356e0d0de21cdc348c3394084d90939b85e4246c4d14c76aee6ca4f46646ea98"

YOMITAN_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
YOMITAN_MAX_ZIP_ENTRIES = 250
YOMITAN_MAX_INDEX_BYTES = 512 * 1024
YOMITAN_MAX_ENTRY_BYTES = 8 * 1024 * 1024
YOMITAN_MAX_TOTAL_BYTES = 64 * 1024 * 1024
YOMITAN_MAX_TERMS = 500_000
YOMITAN_MAX_FIELD_CHARS = 1_024
YOMITAN_MAX_GLOSSARY_CHARS = 16_384

TORCH_CPU_WHEELS = {
    ("3.14", "x86_64"): "torch @ https://download.pytorch.org/whl/cpu/torch-2.9.1%2Bcpu-cp314-cp314-manylinux_2_28_x86_64.whl#sha256=88adf5157db5da1d54b1c9fe4a6c1d20ceef00e75d854e206a87dbf69e3037dc",
    ("3.14", "aarch64"): "torch @ https://download.pytorch.org/whl/cpu/torch-2.9.1%2Bcpu-cp314-cp314-manylinux_2_28_aarch64.whl#sha256=65010ab4aacce6c9a1ddfc935f986c003ca8638ded04348fd326c3e74346237c",
}


def _normalize_arch(machine=None):
    """Normalize platform.machine() values to wheel arch names."""
    arch = (machine or platform.machine() or "").lower()
    if arch in ("amd64", "x86_64"):
        return "x86_64"
    if arch in ("arm64", "aarch64"):
        return "aarch64"
    return arch


def _torch_requirement(machine=None, py_version=None):
    """Return (requirement_spec, extra_pip_args) for a hash-pinned CPU PyTorch wheel."""
    arch = _normalize_arch(machine)
    ver = py_version or f"{sys.version_info.major}.{sys.version_info.minor}"
    pinned = TORCH_CPU_WHEELS.get((ver, arch))
    if not pinned:
        raise RuntimeError(
            f"Unsupported Python/architecture for verified dependency install: Python {ver} on {arch}. "
            "Supported targets: Python 3.14 on x86_64 or aarch64."
        )
    return pinned, []


def _sanitize_tsv_cell(value) -> str:
    """Escape leading spreadsheet formula triggers while keeping scalar text intact."""
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _read_zip_entry_limited(zf: zipfile.ZipFile, info: zipfile.ZipInfo, max_bytes: int) -> bytes:
    """Read a zip member bounded to max_bytes even if central directory metadata lies."""
    if info.file_size > max_bytes:
        raise ValueError(f"Archive entry '{info.filename}' exceeds {max_bytes} byte limit")
    with zf.open(info, "r") as fp:
        data = fp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"Archive entry '{info.filename}' decompressed size exceeds {max_bytes} byte limit")
    return data


_UI_TORCH_SPEC = TORCH_CPU_WHEELS.get((f"{sys.version_info.major}.{sys.version_info.minor}", _normalize_arch()), TORCH_CPU_WHEELS[("3.14", "x86_64")])

COMPONENT_LOCK_NAMES = {
    "dictionary": {"jamdict", "jamdict-data", "chirptext", "puchikarui"},
    "segmentation": {"fugashi", "unidic-lite"},
    "ocr": {
        "torch", "meikiocr", "manga-ocr", "onnxruntime", "opencv-python-headless",
        "huggingface-hub", "transformers", "pillow", "numpy", "certifi",
        "charset-normalizer", "filelock", "fire", "flatbuffers", "fsspec",
        "hf-xet", "idna", "jaconv", "jinja2", "loguru", "markupsafe",
        "mpmath", "networkx", "packaging", "protobuf", "pyperclip", "pyyaml",
        "regex", "requests", "safetensors", "setuptools", "sympy", "termcolor",
        "tokenizers", "tqdm", "typing-extensions", "urllib3",
    },
    "input": {"evdev"},
}

COMPONENTS = {
    "dictionary": {
        "id": "dictionary",
        "name": "JMDict + KANJIDIC2",
        "desc": "Japanese-English definitions (~200k words) & kanji details",
        "packages": ["jamdict==0.1a11.post2", "jamdict-data==1.5"],
        "check_module": "jamdict",
        "size": "~35 MB",
    },
    "segmentation": {
        "id": "segmentation",
        "name": "MeCab + UniDic (Tokenizer)",
        "desc": "Breaks sentences into words & kana readings",
        "packages": ["fugashi==1.5.2", "unidic-lite==1.0.8"],
        "check_module": "fugashi",
        "size": "~55 MB",
    },
    "ocr": {
        "id": "ocr",
        "name": "Japanese OCR Engines (MeikiOCR + Manga-OCR)",
        "desc": "Fast game/VN ONNX OCR (MeikiOCR) & manga fallback (Manga-OCR)",
        "packages": [_UI_TORCH_SPEC, "manga-ocr==0.1.16", "meikiocr==0.3.5", "onnxruntime==1.32.0", "opencv-python-headless==5.1.0.95", "huggingface-hub==0.36.2", "transformers==4.57.6", "pillow==13.0.0"],
        "check_module": "meikiocr",
        "size": "CPU runtime + ONNX/PyTorch models",
    },
    "input": {
        "id": "input",
        "name": "Trackpad Controller (evdev)",
        "desc": "Trackpad drag-to-select regions via evdev",
        "packages": ["evdev==2.0.0"],
        "check_module": "evdev",
        "size": "<1 MB",
    },
}


def traced(method):
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        request = uuid.uuid4().hex[:8]
        started = time.monotonic()
        self._diagnostic("start", method.__name__, request)
        try:
            result = await method(self, *args, **kwargs)
            status = "error_response" if isinstance(result, dict) and result.get("error") else "end"
            self._diagnostic(status, method.__name__, request, elapsed_ms=round((time.monotonic()-started)*1000), error=str(result.get("error", ""))[:2000] if isinstance(result, dict) else "")
            return result
        except BaseException:
            self._diagnostic("exception", method.__name__, request, traceback=traceback.format_exc()[-6000:])
            raise
    return wrapper


class Plugin:
    """Decky Yomi — Japanese Learning Plugin Backend"""

    # ─── State ───────────────────────────────────────────────────
    config: dict = {}
    config_path: str = ""
    vocab_db_path: str = ""
    data_dir: str = ""
    py_modules_dir: str = ""
    trackpad_task = None
    install_process = None
    install_state: dict = {
        "status": "idle",
        "component": None,
        "current_step": "",
        "percent": 0,
        "logs": [],
        "error": None,
    }

    # ════════════════════════════════════════════════════════════
    #  LIFECYCLE
    # ════════════════════════════════════════════════════════════

    def _diagnostic(self, event, operation, request="", **fields):
        logger = getattr(self, "diagnostic_logger", None)
        if logger:
            logger.info(json.dumps({"time": datetime.now().astimezone().isoformat(), "pid": os.getpid(), "event": event, "operation": operation, "request": request, **fields}, ensure_ascii=False))

    async def log_frontend(self, lines: list):
        for line in lines[:25]:
            self._diagnostic("frontend", "browser", message=str(line)[:6000])

    @traced
    async def _main(self):
        """Initialize plugin backend."""
        decky.logger.info("Decky Yomi: Initializing...")

        # Set up paths — DECKY_PLUGIN_SETTINGS_DIR persists across updates
        self.data_dir = decky.DECKY_PLUGIN_SETTINGS_DIR
        os.makedirs(self.data_dir, exist_ok=True)
        self.diagnostic_logger = logging.getLogger("decky_yomi.diagnostics")
        self.diagnostic_logger.setLevel(logging.INFO)
        self.diagnostic_logger.propagate = False
        for handler in self.diagnostic_logger.handlers[:]:
            handler.close()
            self.diagnostic_logger.removeHandler(handler)
        handler = RotatingFileHandler(os.path.join(self.data_dir, "diagnostics.jsonl"), maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        self.diagnostic_logger.addHandler(handler)
        self._diagnostic("startup", "backend", version="0.1.9", python=sys.version)

        self.config_path = os.path.join(self.data_dir, "config.json")
        self.vocab_db_path = os.path.join(self.data_dir, "vocab.db")
        self.py_modules_dir = os.path.join(self.data_dir, "py_modules")
        os.makedirs(self.py_modules_dir, exist_ok=True)

        if self.py_modules_dir not in sys.path:
            sys.path.insert(0, self.py_modules_dir)

        # Load config
        self._load_config()

        # Initialize vocabulary database
        self._init_vocab_db()

        # Lazy-load OCR and dictionary engines in background
        asyncio.get_event_loop().create_task(self._init_engines())

        # Start controller button monitor for low-level shortcut detection (L4/R4/etc.)
        if ControllerButtonMonitor:
            self.controller_monitor = ControllerButtonMonitor(logger=decky.logger)
            started = self.controller_monitor.start()
            decky.logger.info(f"Decky Yomi: Controller button monitor started={started}")
        else:
            decky.logger.warning("Decky Yomi: ControllerButtonMonitor class not available")

        decky.logger.info("Decky Yomi: Backend ready!")

    @traced
    async def _unload(self):
        """Clean up on plugin unload."""
        decky.logger.info("Decky Yomi: Unloading...")
        if getattr(self, "controller_monitor", None):
            self.controller_monitor.stop()
        proc = getattr(self, "engine_process", None)
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        if self.trackpad_task and not self.trackpad_task.done():
            self.trackpad_task.cancel()
        if self.install_process:
            try:
                self.install_process.terminate()
            except Exception:
                pass

    # ════════════════════════════════════════════════════════════
    #  ENGINE INITIALIZATION
    # ════════════════════════════════════════════════════════════

    async def _worker_call(self, method, *args):
        if not hasattr(self, "worker_lock"):
            self.worker_lock = asyncio.Lock()
        async with self.worker_lock:
            proc = getattr(self, "engine_process", None)
            if proc is None or proc.returncode is not None:
                python, env = self._installer_runtime()
                env["HF_HOME"] = os.path.join(self.data_dir, "models")
                proc = await asyncio.create_subprocess_exec(
                    python, "-I", os.path.join(PLUGIN_DIR, "backend", "engine_worker.py"),
                    PLUGIN_DIR, self.data_dir, env=env,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, limit=4 * 1024 * 1024,
                )
                self.engine_process = proc
                self.worker_log_task = asyncio.create_task(self._worker_logs(proc))
            try:
                proc.stdin.write((json.dumps({"method": method, "args": args, "config": self.config}) + "\n").encode())
                await proc.stdin.drain()
                line = await asyncio.wait_for(proc.stdout.readline(), 600)
                if not line:
                    raise RuntimeError("Engine worker stopped; see diagnostics for details")
                response = json.loads(line)
                if response.get("error"):
                    raise RuntimeError(response["error"])
                return response["result"]
            except BaseException:
                if proc.returncode is None:
                    proc.kill()
                await proc.wait()
                raise

    async def _worker_logs(self, proc):
        while line := await proc.stderr.readline():
            self._diagnostic("engine.worker", "stderr", message=line.decode(errors="replace")[:6000])

    @traced
    async def _init_engines(self):
        """Initialize OCR, morphological analyzer, and dictionary."""
        if getattr(sys, "frozen", False):
            self.worker_status = await self._worker_call("_init_engines")
            return
        global manga_ocr_engine, meiki_ocr_engine, morphological_tagger, dictionary_engine

        # --- OCR Engine 1: MeikiOCR (Fast Game/VN ONNX) ---
        try:
            self._diagnostic("engine.begin", "meikiocr")
            from meikiocr import MeikiOCR
            meiki_ocr_engine = MeikiOCR(provider="CPUExecutionProvider")
            decky.logger.info("Decky Yomi: meikiocr loaded")
        except ImportError:
            decky.logger.info("meikiocr not installed — using manga-ocr")
        except Exception as e:
            decky.logger.error(traceback.format_exc())
            decky.logger.error(f"Failed to load meikiocr engine: {e}")

        # --- OCR Engine 2: manga-ocr (Fallback / Manga) ---
        try:
            self._diagnostic("engine.begin", "ocr")
            from manga_ocr import MangaOcr
            manga_ocr_engine = MangaOcr(force_cpu=True)
            decky.logger.info("Decky Yomi: manga-ocr loaded")
        except ImportError:
            decky.logger.error(traceback.format_exc())
            decky.logger.warning(
                "manga-ocr not available — OCR will be disabled. "
                "Run backend/install_deps.sh to install."
            )
        except Exception as e:
            decky.logger.error(traceback.format_exc())
            decky.logger.error(f"Failed to load OCR engine: {e}")

        # --- Morphological Analyzer (fugashi / MeCab) ---
        try:
            self._diagnostic("engine.begin", "tokenizer")
            from fugashi import Tagger
            morphological_tagger = Tagger()
            decky.logger.info("Decky Yomi: MeCab/fugashi loaded")
        except ImportError:
            decky.logger.error(traceback.format_exc())
            decky.logger.warning("fugashi not available — word segmentation disabled")
        except Exception as e:
            decky.logger.error(traceback.format_exc())
            decky.logger.error(f"Failed to load MeCab: {e}")

        # --- Dictionary (jamdict → JMDict + KANJIDIC2) ---
        try:
            self._diagnostic("engine.begin", "dictionary")
            from jamdict import Jamdict
            dictionary_engine = Jamdict()
            decky.logger.info("Decky Yomi: JMDict/KANJIDIC2 loaded")
        except ImportError:
            decky.logger.error(traceback.format_exc())
            decky.logger.warning("jamdict not available — dictionary disabled")
        except Exception as e:
            decky.logger.error(traceback.format_exc())
            decky.logger.error(f"Failed to load dictionary: {e}")

    @traced
    async def get_engine_status(self) -> dict:
        """Return which engines are currently loaded."""
        if getattr(sys, "frozen", False):
            proc = getattr(self, "engine_process", None)
            return getattr(self, "worker_status", {"ocr": False, "segmentation": False, "dictionary": False}) if proc and proc.returncode is None else {"ocr": False, "segmentation": False, "dictionary": False}
        return {
            "ocr": (meiki_ocr_engine is not None) or (manga_ocr_engine is not None),
            "meikiocr": meiki_ocr_engine is not None,
            "manga_ocr": manga_ocr_engine is not None,
            "segmentation": morphological_tagger is not None,
            "dictionary": dictionary_engine is not None,
        }

    # ════════════════════════════════════════════════════════════
    #  IN-UI DOWNLOAD & DEPENDENCY INSTALLER
    # ════════════════════════════════════════════════════════════

    def _is_module_available(self, module_name: str) -> bool:
        """Check if a python module is importable."""
        try:
            import importlib
            importlib.invalidate_caches()
            import importlib.util
            spec = importlib.util.find_spec(module_name)
            return spec is not None
        except Exception:
            return False

    def _can_build_evdev(self) -> bool:
        """Return True if evdev is importable or a C compiler and kernel input headers exist."""
        if self._is_module_available("evdev"):
            return True
        has_cc = bool(shutil.which("gcc") or shutil.which("cc"))
        return has_cc and os.path.exists("/usr/include/linux/input.h")

    @traced
    async def get_components_status(self) -> dict:
        """Return detailed status for all downloadable components."""
        components_list = []
        readiness = await self.get_engine_status()
        all_installed = True
        can_build_evdev = self._can_build_evdev()

        for c_id, c in COMPONENTS.items():
            installed = readiness.get(c_id, self._is_module_available(c["check_module"]))
            if not installed and (c_id != "input" or can_build_evdev):
                all_installed = False
            components_list.append({
                "id": c["id"],
                "name": c["name"],
                "desc": c["desc"],
                "installed": installed,
                "size": c["size"],
                "packages": c["packages"],
            })

        return {
            "components": components_list,
            "all_installed": all_installed,
            "install_dir": self.py_modules_dir,
            "installing": self.install_state["status"] == "installing",
            "install_progress": self.install_state,
        }

    @traced
    async def start_install(self, component_id: str = "all") -> dict:
        """
        Trigger package download & installation directly from the UI.
        component_id can be 'all' or one of 'dictionary', 'segmentation', 'ocr', 'input'.
        """
        if self.install_state["status"] == "installing":
            return {"started": False, "error": "Installation already in progress"}

        selected_ids = []
        packages = []
        skipped_notes = []
        if component_id == "all":
            for c_id, c in COMPONENTS.items():
                if self._is_module_available(c["check_module"]):
                    continue
                if c_id == "input" and not self._can_build_evdev():
                    skipped_notes.append(
                        "Skipping optional 'input' (evdev): C compiler and /usr/include/linux/input.h are unavailable."
                    )
                    continue
                selected_ids.append(c_id)
                packages.extend(c["packages"])
            if not packages:
                return {"started": False, "error": "All components are already installed!"}
        elif component_id in COMPONENTS:
            if component_id == "input" and not self._can_build_evdev():
                return {
                    "started": False,
                    "error": "Cannot build evdev: install gcc and linux-libc-dev (/usr/include/linux/input.h). Preset capture works without it.",
                }
            selected_ids = [component_id]
            packages = COMPONENTS[component_id]["packages"]
        else:
            return {"started": False, "error": f"Unknown component: {component_id}"}

        self.install_state = {
            "status": "installing",
            "component": component_id,
            "current_step": "Starting package download...",
            "percent": 5,
            "logs": [
                f"Target: {self.py_modules_dir}",
                f"Packages: {', '.join(packages)}",
                *skipped_notes,
            ],
            "error": None,
        }

        asyncio.create_task(self._run_install_task(packages, component_id, selected_ids))
        return {"started": True, "component": component_id}

    def _installer_runtime(self):
        # sys.executable is PluginLoader in Decky's frozen runtime, NOT Python.
        python = "/usr/bin/python3"
        if not os.path.isfile(python):
            raise RuntimeError("Standalone /usr/bin/python3 is required for dependency installation.")
        env = os.environ.copy()
        for key in ("PYTHONHOME", "PYTHONPATH"):
            env.pop(key, None)
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        env.pop("LD_LIBRARY_PATH", None)
        if original:
            env["LD_LIBRARY_PATH"] = original
        return python, env

    async def _run_tracked_subprocess(self, cmd: list, env: dict, timeout: float):
        """Run a subprocess while tracking it for cancellation, returning (returncode, combined_output)."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self.install_process = proc
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        finally:
            self.install_process = None
        return proc.returncode, stdout.decode(errors="replace")

    async def _is_venv_ready(self, venv_python: str, env: dict) -> bool:
        """Return True if an existing private venv matches our Python minor version and has pip."""
        if not os.path.isfile(venv_python):
            return False
        try:
            await self._verify_installer_runtime(venv_python, env)
            return True
        except Exception:
            return False

    @staticmethod
    def _download_get_pip(dest_path: str):
        with urllib.request.urlopen(GET_PIP_URL, timeout=60) as resp:
            data = resp.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != GET_PIP_SHA256:
            raise ValueError(
                f"get-pip.py SHA-256 verification failed: expected {GET_PIP_SHA256}, got {digest}"
            )
        with open(dest_path, "wb") as f:
            f.write(data)

    @staticmethod
    def _parse_lockfile_blocks(lock_path: str) -> list:
        """Return [(normalized_pkg_name, raw_block_text), ...] from a hash-pinned lockfile."""
        if not os.path.exists(lock_path):
            return []
        blocks = []
        current = []
        with open(lock_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                stripped = raw_line.strip()
                if not current and (not stripped or stripped.startswith("#")):
                    continue
                if stripped.endswith("\\"):
                    current.append(raw_line.rstrip("\n"))
                    continue
                current.append(raw_line.rstrip("\n"))
                block_text = "\n".join(current)
                first = current[0].strip()
                pkg_name = (
                    first.split("==")[0]
                    .split(" @ ")[0]
                    .split(";")[0]
                    .strip()
                    .lower()
                    .replace("_", "-")
                )
                blocks.append((pkg_name, block_text))
                current = []
        return blocks

    def _write_locked_requirements(self, selected_ids: list, dest_path: str) -> str:
        """Write a subset of hash-pinned entries from backend/requirements*.txt for selected components."""
        wanted = set()
        for cid in selected_ids:
            wanted.update(COMPONENT_LOCK_NAMES.get(cid, set()))
        if "ocr" in selected_ids:
            # Verify Python/arch support cleanly instead of falling back to unpinned indexes
            _torch_requirement()

        lock_paths = [os.path.join(BACKEND_DIR, "requirements.txt")]
        if "input" in selected_ids:
            lock_paths.append(os.path.join(BACKEND_DIR, "requirements-input.txt"))

        kept = []
        for path in lock_paths:
            for pkg_name, block_text in self._parse_lockfile_blocks(path):
                if pkg_name in wanted:
                    kept.append(block_text)
        if not kept:
            raise RuntimeError(f"No hash-pinned requirements matched components: {selected_ids}")
        with open(dest_path, "w", encoding="utf-8") as f:
            f.write("\n".join(kept) + "\n")
        return dest_path

    @traced
    async def _prepare_installer_runtime(self):
        python, env = self._installer_runtime()
        temp_dir = os.path.join(self.data_dir, "installer-tmp")
        os.makedirs(temp_dir, exist_ok=True)
        env["TMPDIR"] = temp_dir
        runtime_dir = os.path.join(self.data_dir, "installer-venv")
        venv_python = os.path.join(runtime_dir, "bin", "python")

        if await self._is_venv_ready(venv_python, env):
            return venv_python, env

        # Clear any half-created venv first, then fall back to get-pip.py when python3-venv isn't installed.
        self.install_state["current_step"] = "Preparing private Python environment and pip..."
        rc, out = await self._run_tracked_subprocess([python, "-I", "-m", "venv", "--clear", runtime_dir], env, 120)
        if rc != 0:
            rc2, out2 = await self._run_tracked_subprocess(
                [python, "-I", "-m", "venv", "--clear", "--without-pip", runtime_dir], env, 120
            )
            if rc2 != 0:
                combined = (out + "\n" + out2).strip()[-2000:]
                raise RuntimeError("Could not prepare private pip environment: " + combined)
            self.install_state["current_step"] = "Bootstrapping pip into private environment..."
            get_pip_path = os.path.join(temp_dir, "get-pip.py")
            try:
                await asyncio.to_thread(self._download_get_pip, get_pip_path)
            except Exception as e:
                hint = " (or install python3-venv)" if "ensurepip" in out else ""
                raise RuntimeError(f"Could not download pip bootstrapper{hint}: {e}") from e
            rc3, out3 = await self._run_tracked_subprocess(
                [venv_python, "-I", get_pip_path, "--no-cache-dir"], env, 180
            )
            if rc3 != 0:
                raise RuntimeError("Could not bootstrap pip into private environment: " + out3.strip()[-2000:])

        await self._verify_installer_runtime(venv_python, env)
        return venv_python, env

    @traced
    async def _verify_installer_runtime(self, python, env):
        proc = await asyncio.create_subprocess_exec(
            python, "-I", "-c", "import sys, pip; print('%d.%d' % sys.version_info[:2])",
            env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), 15)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        expected = "%d.%d" % sys.version_info[:2]
        out_str = stdout.decode(errors="replace").strip()
        err_str = stderr.decode(errors="replace").strip()
        if proc.returncode or out_str != expected:
            detail = (err_str or out_str)[-1000:]
            raise RuntimeError("Dependency setup requires standalone Python " + expected + " with pip. " + detail)
        self._diagnostic("installer.runtime", "pip", executable=python, python=expected)

    @traced
    async def _run_install_task(self, packages: list, component_id: str, selected_ids: list | None = None):
        """Background worker that runs pip install --target --require-hashes and streams progress."""
        try:
            decky.logger.info(f"Decky Yomi: Starting in-UI download for: {packages}")
            os.makedirs(self.py_modules_dir, exist_ok=True)

            python, env = await self._prepare_installer_runtime()
            temp_dir = os.path.join(self.data_dir, "installer-tmp")
            os.makedirs(temp_dir, exist_ok=True)
            ids = selected_ids or ([c for c in COMPONENTS if c != "input" or self._can_build_evdev()] if component_id == "all" else [component_id])
            req_path = os.path.join(temp_dir, f"locked-{component_id}.txt")
            self._write_locked_requirements(ids, req_path)
            cmd = [
                python,
                "-I",
                "-m",
                "pip",
                "install",
                f"--target={self.py_modules_dir}",
                "--no-cache-dir",
                "--upgrade",
                "--require-hashes",
                "-r",
                req_path,
            ]

            self.install_state["logs"].append(f"Command: {' '.join(cmd)}")
            self.install_state["current_step"] = "Downloading packages from PyPI..."
            self.install_state["percent"] = 15

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self.install_process = proc

            total_pkgs = len(packages)
            current_pkg_idx = 0

            # Stream pip output in real time
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if line:
                    self._diagnostic("installer.output", "pip", message=line[:4000])
                    self.install_state["logs"].append(line)
                    if len(self.install_state["logs"]) > 50:
                        self.install_state["logs"].pop(0)

                    # Dynamic progress detection
                    if "Collecting " in line:
                        pkg_name = line.split("Collecting ")[-1].split(" ")[0]
                        current_pkg_idx += 1
                        pct = min(85, int(15 + (current_pkg_idx / (total_pkgs + 1)) * 60))
                        self.install_state["percent"] = pct
                        self.install_state["current_step"] = f"Collecting {pkg_name}..."
                    elif "Downloading " in line:
                        short_dl = line.split("/")[-1] if "/" in line else line
                        self.install_state["current_step"] = f"Downloading: {short_dl[:50]}"
                    elif "Installing collected packages:" in line:
                        self.install_state["percent"] = 88
                        self.install_state["current_step"] = "Unpacking & installing into py_modules..."
                    elif "Successfully installed" in line:
                        self.install_state["percent"] = 94
                        self.install_state["current_step"] = "Installation successful!"

            await proc.wait()
            self.install_process = None

            if proc.returncode == 0:
                self.install_state["percent"] = 96
                self.install_state["current_step"] = "Reloading engines into memory..."
                decky.logger.info("Decky Yomi: Pip completed. Reloading engines...")

                # Invalidate import caches and reload engines
                if self.py_modules_dir not in sys.path:
                    sys.path.insert(0, self.py_modules_dir)
                import importlib
                importlib.invalidate_caches()

                await self._init_engines()

                readiness = await self.get_engine_status()
                required = ("ocr", "segmentation", "dictionary") if component_id == "all" else (component_id,)
                failed = [key for key in required if key in readiness and not readiness[key]]
                if failed:
                    raise RuntimeError("Installed packages but engines failed to load: " + ", ".join(failed))
                self.install_state["percent"] = 100
                self.install_state["status"] = "completed"
                self.install_state["current_step"] = "All components ready to use!"
                decky.logger.info("Decky Yomi: In-UI download completed successfully!")
            else:
                self.install_state["status"] = "error"
                self.install_state["error"] = f"pip exited with code {proc.returncode}"
                self.install_state["current_step"] = "Installation failed. Check logs."
                decky.logger.error(f"Decky Yomi: pip failed with rc={proc.returncode}")

        except Exception as e:
            self.install_state["status"] = "error"
            self.install_state["error"] = str(e)
            self.install_state["current_step"] = f"Error: {e}"
            decky.logger.error(f"Decky Yomi: In-UI install exception: {e}")

    @traced
    async def get_install_progress(self) -> dict:
        """Return the current install progress dictionary."""
        return self.install_state

    @traced
    async def cancel_install(self) -> dict:
        """Cancel an ongoing download / install process."""
        if self.install_process:
            try:
                self.install_process.terminate()
                self.install_state["status"] = "error"
                self.install_state["error"] = "Cancelled by user"
                self.install_state["current_step"] = "Download cancelled"
                return {"cancelled": True}
            except Exception as e:
                return {"cancelled": False, "error": str(e)}
        return {"cancelled": False, "error": "No installation running"}

    # ════════════════════════════════════════════════════════════
    #  SCREEN CAPTURE
    # ════════════════════════════════════════════════════════════

    def _get_x11_display(self) -> str:
        """Find the active X11 display for screen capture."""
        candidates = self._get_candidate_displays()
        return candidates[0] if candidates else ":0"

    def _get_candidate_displays(self) -> list:
        """Find candidate active X11 displays for screen capture.

        In SteamOS Game Mode (gamescope --xwayland-count 2):
          :0 is the Steam CEF / overlay display (1920x1080 UI canvas).
          :1 is the actual running game client (e.g. Proton, Wine, native Linux game).
        We prioritize :1 over :0 so screen capture targets the active game,
        not the Steam UI menu.
        """
        displays = []
        # In Game Mode, :1 is the dedicated game display
        if os.path.exists("/tmp/.X11-unix/X1"):
            displays.append(":1")
        for num in range(2, 6):
            d = f":{num}"
            if os.path.exists(f"/tmp/.X11-unix/X{num}") and d not in displays:
                displays.append(d)
        if os.path.exists("/tmp/.X11-unix/X0") and ":0" not in displays:
            displays.append(":0")
        current_display = os.environ.get("DISPLAY")
        if current_display and current_display not in displays:
            displays.append(current_display)
        if ":0" not in displays:
            displays.append(":0")
        return displays

    def get_configured_region(self) -> Optional[dict]:
        """Resolve capture region from configured preset or custom coordinates."""
        mode = self.config.get("ocr_mode", "bottom_30")
        if mode in ("fullscreen", "full"):
            return None
        elif mode == "bottom_30":
            return {"x": 0, "y": 70, "w": 100, "h": 30, "unit": "percent"}
        elif mode == "bottom_40":
            return {"x": 0, "y": 60, "w": 100, "h": 40, "unit": "percent"}
        elif mode == "center":
            return {"x": 15, "y": 25, "w": 70, "h": 50, "unit": "percent"}
        elif mode == "custom":
            try:
                x = float(self.config.get("custom_region_x", 0))
                y = float(self.config.get("custom_region_y", 70))
                w = float(self.config.get("custom_region_w", 100))
                h = float(self.config.get("custom_region_h", 30))
                return {"x": x, "y": y, "w": w, "h": h, "unit": "percent"}
            except (TypeError, ValueError):
                return {"x": 0, "y": 70, "w": 100, "h": 30, "unit": "percent"}
        elif mode == "region":
            # Backward-compatibility for existing configs
            return {"x": 0, "y": 70, "w": 100, "h": 30, "unit": "percent"}
        return None

    def _crop_image(self, path: str, region: dict):
        """Crop an image file in-place to the given region (supports pixels or percentages)."""
        if not region or not all(k in region for k in ("x", "y", "w", "h")):
            return
        try:
            from PIL import Image
            with Image.open(path) as im:
                im_w, im_h = im.size
                is_percent = region.get("unit") in ("percent", "%") or (
                    region.get("unit") is None
                    and 0 <= float(region["x"]) <= 100
                    and 0 <= float(region["y"]) <= 100
                    and 0 < float(region["w"]) <= 100
                    and 0 < float(region["h"]) <= 100
                    and (im_w > 200 and im_h > 200)
                )

                if is_percent:
                    rx = float(region["x"])
                    ry = float(region["y"])
                    rw = float(region["w"])
                    rh = float(region["h"])
                    x = int(round(im_w * (rx / 100.0)))
                    y = int(round(im_h * (ry / 100.0)))
                    w = int(round(im_w * (rw / 100.0)))
                    h = int(round(im_h * (rh / 100.0)))
                else:
                    x = int(region["x"])
                    y = int(region["y"])
                    w = int(region["w"])
                    h = int(region["h"])

                x = max(0, min(x, im_w - 1))
                y = max(0, min(y, im_h - 1))
                w = max(1, min(w, im_w - x))
                h = max(1, min(h, im_h - y))

                if w > 10 and h > 10:
                    cropped = im.crop((x, y, x + w, y + h))
                    cropped.save(path)
                    decky.logger.info(f"Cropped screenshot ({im_w}x{im_h}) to ({x}, {y}, {w}, {h})")
        except Exception as e:
            decky.logger.warning(f"Failed to crop region from screenshot: {e}")

    def _session_cmd_and_env(self, cmd: list, display: str = None) -> tuple:
        """Build command/env for screen capture, dropping to user 'deck' only when it actually exists."""
        env = dict(os.environ)
        if display:
            env["DISPLAY"] = display
        if "XDG_RUNTIME_DIR" not in env and os.path.exists("/run/user/1000"):
            env["XDG_RUNTIME_DIR"] = "/run/user/1000"
        if os.geteuid() == 0 and shutil.which("runuser"):
            try:
                import pwd
                pwd.getpwnam("deck")
                cmd = ["runuser", "-u", "deck", "--"] + cmd
            except KeyError:
                pass
        return cmd, env

    async def _capture_pipewire(self, output_path: str, region: dict = None) -> bool:
        """Capture screen using GStreamer pipewiresrc (native Gamescope compositor capture on SteamOS)."""
        gst_bin = shutil.which("gst-launch-1.0") or "/usr/bin/gst-launch-1.0"
        if not os.path.exists(gst_bin):
            return False

        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass

        cmd = [
            gst_bin,
            "-e",
            "pipewiresrc",
            "do-timestamp=true",
            "num-buffers=5",
            "!",
            "videoconvert",
            "!",
            "pngenc",
            "snapshot=true",
            "!",
            "filesink",
            f"location={output_path}",
        ]
        cmd, env = self._session_cmd_and_env(cmd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
                if region and all(k in region for k in ("x", "y", "w", "h")):
                    self._crop_image(output_path, region)
                decky.logger.info(f"Screen captured via PipeWire → {output_path} ({os.path.getsize(output_path)} bytes)")
                return True
            err_msg = stderr.decode(errors="replace")[-300:].strip()
            decky.logger.warning(f"PipeWire capture failed (rc={proc.returncode}): {err_msg}")
        except Exception as e:
            decky.logger.warning(f"PipeWire capture exception: {e}")

        return False

    @traced
    async def capture_screen(self, region: dict = None) -> str:
        """
        Capture the screen (or a region) using the best available tool on SteamOS.
        Primary: GStreamer pipewiresrc (Gamescope compositor stream, instant & pixel-perfect).
        Fallback 1: ffmpeg x11grab.
        Fallback 2: grim (Wayland screencopy protocol).
        Fallback 3: spectacle (KDE Plasma desktop mode).

        Args:
            region: Optional {"x": int, "y": int, "w": int, "h": int}
        Returns:
            Path to the captured PNG, or error string starting with "ERROR:"
        """
        output_path = os.path.join(self.data_dir, "capture.png")
        if region is None:
            region = self.get_configured_region()

        # 1. Primary: GStreamer pipewiresrc (native to Gamescope/SteamOS)
        if shutil.which("gst-launch-1.0"):
            success = await self._capture_pipewire(output_path, region)
            if success:
                return output_path

        # 2. Secondary fallback: ffmpeg x11grab
        if shutil.which("ffmpeg"):
            success = await self._capture_ffmpeg(output_path, region)
            if success:
                decky.logger.info(f"Screen captured via ffmpeg → {output_path}")
                return output_path

        # 3. Fallback: grim (custom Wayland session)
        if shutil.which("grim"):
            success = await self._capture_grim(output_path, region)
            if success:
                decky.logger.info(f"Screen captured via grim → {output_path}")
                return output_path

        # 4. Fallback: spectacle (KDE Plasma desktop mode)
        if shutil.which("spectacle"):
            success = await self._capture_spectacle(output_path, region)
            if success:
                decky.logger.info(f"Screen captured via spectacle → {output_path}")
                return output_path

        return (
            "ERROR: No working screenshot tool found. Checked GStreamer PipeWire, ffmpeg, grim, and spectacle. "
            "Please ensure gst-launch-1.0 or ffmpeg is present on SteamOS."
        )

    async def _capture_ffmpeg(self, output_path: str, region: dict = None) -> bool:
        """Capture screen using ffmpeg x11grab across candidate displays."""
        ffmpeg_bin = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
        candidate_displays = self._get_candidate_displays()

        for display in candidate_displays:
            cmd = [ffmpeg_bin, "-y", "-f", "x11grab", "-i", display, "-vframes", "1", output_path]
            cmd, env = self._session_cmd_and_env(cmd, display=display)

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                _, stderr = await proc.communicate()
                if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    if region and all(k in region for k in ("x", "y", "w", "h")):
                        self._crop_image(output_path, region)
                    decky.logger.info(f"ffmpeg captured screen on display {display} → {output_path}")
                    return True
                err_msg = stderr.decode(errors="replace")[-500:].strip()
                decky.logger.warning(f"ffmpeg capture failed on {display} (rc={proc.returncode}): {err_msg}")
            except Exception as e:
                decky.logger.warning(f"ffmpeg capture exception on {display}: {e}")

        return False

    async def _capture_grim(self, output_path: str, region: dict = None) -> bool:
        """Capture screen using grim."""
        cmd = ["grim"]
        if region and all(k in region for k in ("x", "y", "w", "h")):
            geometry = f"{region['x']},{region['y']} {region['w']}x{region['h']}"
            cmd.extend(["-g", geometry])
        cmd.append(output_path)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True
            decky.logger.warning(f"grim capture failed (rc={proc.returncode}): {stderr.decode(errors='replace')[:200]}")
        except Exception as e:
            decky.logger.warning(f"grim exception: {e}")
        return False

    async def _capture_spectacle(self, output_path: str, region: dict = None) -> bool:
        """Capture screen using spectacle."""
        display = self._get_x11_display()
        cmd, env = self._session_cmd_and_env(
            ["spectacle", "-b", "-n", "-f", "-o", output_path],
            display=display,
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await proc.communicate()
            if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                if region and all(k in region for k in ("x", "y", "w", "h")):
                    self._crop_image(output_path, region)
                return True
        except Exception:
            pass
        return False

    # ════════════════════════════════════════════════════════════
    #  OCR PROCESSING
    # ════════════════════════════════════════════════════════════

    def _detect_text_boxes_2d(self, img):
        """Find tight text-region crops in large/fullscreen captures using gradient connected components."""
        from collections import deque
        import numpy as np
        from PIL import Image, ImageFilter

        w, h = img.size
        arr = np.array(img.convert("L"), dtype=np.int16)
        gx = np.abs(np.diff(arr, axis=1, prepend=arr[:, :1]))
        gy = np.abs(np.diff(arr, axis=0, prepend=arr[:1, :]))
        grad = np.maximum(gx, gy)
        mask = (grad > 18).astype(np.uint8) * 255

        # Smear horizontally more than vertically so words/lines form boxes without merging distant lines
        m_img = Image.fromarray(mask).filter(ImageFilter.MaxFilter(9))
        # Downsample 4x and bridge small horizontal gaps (~16px)
        dw, dh = max(1, w // 4), max(1, h // 4)
        small = np.array(m_img.resize((dw, dh), Image.NEAREST)) > 0
        # Horizontal 1D dilation of radius 2 on 4x grid (~16px total horizontal bridge)
        dilated = small.copy()
        for shift in (1, 2):
            dilated[:, shift:] |= small[:, :-shift]
            dilated[:, :-shift] |= small[:, shift:]

        vis = np.zeros_like(dilated, dtype=bool)
        boxes = []
        for y in range(dh):
            for x in range(dw):
                if dilated[y, x] and not vis[y, x]:
                    q = deque([(y, x)])
                    vis[y, x] = True
                    y0 = y1 = y
                    x0 = x1 = x
                    cnt = 0
                    while q:
                        cy, cx = q.popleft()
                        cnt += 1
                        if cy < y0:
                            y0 = cy
                        if cy > y1:
                            y1 = cy
                        if cx < x0:
                            x0 = cx
                        if cx > x1:
                            x1 = cx
                        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                            ny, nx = cy + dy, cx + dx
                            if 0 <= ny < dh and 0 <= nx < dw and dilated[ny, nx] and not vis[ny, nx]:
                                vis[ny, nx] = True
                                q.append((ny, nx))
                    bx0, by0, bx1, by1 = x0 * 4, y0 * 4, (x1 + 1) * 4, (y1 + 1) * 4
                    bw, bh = bx1 - bx0, by1 - by0
                    # Filter tiny decorative dots/noise and full-frame borders
                    if 36 <= bw <= w * 0.85 and 20 <= bh <= 180 and cnt >= 24:
                        boxes.append((bx0, by0, bx1, by1))

        # Sort top-to-bottom, left-to-right and cap to keep CPU OCR responsive
        boxes.sort(key=lambda b: (b[1] // 24, b[0]))
        crops = []
        pad = 8
        for bx0, by0, bx1, by1 in boxes[:8]:
            crop = img.crop((max(0, bx0 - pad), max(0, by0 - pad), min(w, bx1 + pad), min(h, by1 + pad)))
            cw, ch = crop.size
            if cw > ch * 3.8:
                chunk_w = max(240, int(ch * 3.5))
                step = max(120, int(chunk_w * 0.85))
                cx = 0
                while cx < cw:
                    cx1 = min(cw, cx + chunk_w)
                    crops.append(crop.crop((cx, 0, cx1, ch)))
                    if cx1 >= cw:
                        break
                    cx += step
            else:
                crops.append(crop)
        return crops

    def _split_ocr_regions(self, img):
        """Split wide, multi-line, or fullscreen captures into compact crops for manga-ocr (224x224 ViT)."""
        from PIL import Image, ImageFilter

        w, h = img.size
        if w <= 0 or h <= 0:
            return [img]
        # Compact single-region crops don't need splitting
        if w <= h * 3.8 and h <= 160:
            return [img]

        # Large / fullscreen captures (e.g. 1280x800): locate individual text regions in 2D first
        if h > 360:
            try:
                crops_2d = self._detect_text_boxes_2d(img)
                if crops_2d:
                    return crops_2d
            except Exception:
                pass

        # Crisp-stroke horizontal line detection + column trimming + valley-snapped chunking
        try:
            import numpy as np

            arr = np.array(img.convert("L"), dtype=np.int16)
            gx = np.abs(np.diff(arr, axis=1, prepend=arr[:, :1]))
            gy = np.abs(np.diff(arr, axis=0, prepend=arr[:1, :]))
            grad = np.maximum(gx, gy)

            strokes = gx >= 38
            row_trans = np.abs(np.diff(strokes.astype(np.int8), axis=1, prepend=0)).sum(axis=1)
            bands = []
            in_band = False
            start_y = 0
            for y, val in enumerate(row_trans):
                if val >= 16 and not in_band:
                    in_band = True
                    start_y = y
                elif val < 16 and in_band:
                    in_band = False
                    bands.append((start_y, y))
            if in_band:
                bands.append((start_y, h))

            merged = []
            for s, e in bands:
                if merged and s - merged[-1][1] <= 2:
                    merged[-1] = (merged[-1][0], e)
                else:
                    merged.append((s, e))
            line_boxes = [(max(0, s - 5), min(h, e + 5)) for s, e in merged if (e - s) >= 12]

            if line_boxes and len(line_boxes) <= 6:
                regions = []
                for y0, y1 in line_boxes:
                    col_energy = (gx[y0:y1, :] >= 38).sum(axis=0)
                    win = np.convolve(col_energy > 0, np.ones(16, dtype=int), mode="same")
                    active = np.where((col_energy > 0) & (win >= 5))[0]
                    if len(active) == 0:
                        continue
                    x0 = max(0, int(active[0]) - 8)
                    x1 = min(w, int(active[-1]) + 8)
                    bw = x1 - x0
                    bh = y1 - y0
                    if bw <= 10 or bh <= 10:
                        continue
                    if bw <= bh * 3.8:
                        regions.append(img.crop((x0, y0, x1, y1)))
                        continue

                    chunk_w = max(220, int(bh * 3.5))
                    overlap = max(24, int(bh * 0.7))
                    cx = x0
                    while cx < x1:
                        target_end = min(x1, cx + chunk_w)
                        if target_end < x1:
                            s0 = max(cx + 80, target_end - 24)
                            s1 = min(x1, target_end + 16)
                            end_x = s0 + int(np.argmin(col_energy[s0:s1])) if s1 > s0 else target_end
                        else:
                            end_x = x1
                        regions.append(img.crop((cx, y0, end_x, y1)))
                        if end_x >= x1:
                            break
                        n0 = max(x0, end_x - overlap)
                        n1 = max(n0 + 1, end_x - 8)
                        next_x = n0 + int(np.argmin(col_energy[n0:n1])) if n1 > n0 else n0
                        cx = max(cx + 60, next_x)
                if regions:
                    return regions
        except Exception:
            pass

        # Fallback: Pillow edge-energy projection without numpy
        edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
        row_energy = list(edges.resize((1, h), Image.BOX).getdata())
        peak = max(row_energy) if row_energy else 0
        thresh = max(4.0, peak * 0.18)

        bands = []
        in_band = False
        start_y = 0
        for y, val in enumerate(row_energy):
            if val >= thresh and not in_band:
                in_band = True
                start_y = y
            elif val < thresh and in_band:
                in_band = False
                bands.append((start_y, y))
        if in_band:
            bands.append((start_y, h))

        merged = []
        for s, e in bands:
            if merged and s - merged[-1][1] <= 8:
                merged[-1] = (merged[-1][0], e)
            else:
                merged.append((s, e))
        line_boxes = [
            (max(0, s - 8), min(h, e + 8))
            for s, e in merged
            if (e - s) >= 14
        ]
        if not line_boxes or len(line_boxes) > 6:
            line_boxes = [(0, h)]

        regions = []
        for y0, y1 in line_boxes:
            band_h = y1 - y0
            if w <= band_h * 3.8:
                regions.append(img.crop((0, y0, w, y1)))
                continue

            chunk_w = max(240, int(band_h * 3.5))
            step = max(120, int(chunk_w * 0.85))
            x = 0
            while x < w:
                x1 = min(w, x + chunk_w)
                regions.append(img.crop((x, y0, x1, y1)))
                if x1 >= w:
                    break
                x += step

        return regions or [img]

    @staticmethod
    def _merge_ocr_chunks(chunks: list) -> str:
        """Merge overlapping OCR chunk strings without duplicating boundary characters."""
        merged = ""
        for chunk in chunks:
            part = (chunk or "").strip()
            if not part:
                continue
            if not merged:
                merged = part
                continue
            overlap = 0
            max_k = min(len(merged), len(part), 6)
            for k in range(max_k, 0, -1):
                if merged.endswith(part[:k]):
                    overlap = k
                    break
            merged += part[overlap:]
        return merged

    @staticmethod
    def _is_kana_only(text: str) -> bool:
        """Return True if text has >=1 char and all non-punctuation chars are hiragana/katakana."""
        has_kana = False
        for ch in text:
            if ch in " \t\r\n、。.,!！?？・…「」『』()（）":
                continue
            if "\u3040" <= ch <= "\u30ff":
                has_kana = True
            else:
                return False
        return has_kana

    @classmethod
    def _filter_furigana_boxes(cls, results: list) -> list:
        """Drop small kana-only ruby furigana lines positioned adjacent to a larger base line."""
        if not results or len(results) < 2:
            return results

        parsed = []
        for idx, item in enumerate(results):
            text = (item.get("text") or "").strip()
            chars = item.get("chars") or []
            if not text or not chars:
                parsed.append(None)
                continue
            xs1 = [c["bbox"][0] for c in chars]
            ys1 = [c["bbox"][1] for c in chars]
            xs2 = [c["bbox"][2] for c in chars]
            ys2 = [c["bbox"][3] for c in chars]
            is_vert = bool(item.get("is_vertical", False))
            sizes = [(c["bbox"][2] - c["bbox"][0]) if is_vert else (c["bbox"][3] - c["bbox"][1]) for c in chars]
            avg_size = sum(sizes) / len(sizes) if sizes else 0.0
            parsed.append({
                "idx": idx,
                "text": text,
                "x1": min(xs1),
                "y1": min(ys1),
                "x2": max(xs2),
                "y2": max(ys2),
                "size": avg_size,
                "is_vert": is_vert,
                "kana_only": cls._is_kana_only(text),
            })

        filtered = []
        for idx, item in enumerate(results):
            meta = parsed[idx]
            if meta is None or not meta["kana_only"]:
                filtered.append(item)
                continue

            is_ruby = False
            for j, other in enumerate(parsed):
                if j == idx or other is None or other["is_vert"] != meta["is_vert"]:
                    continue
                if meta["size"] >= other["size"] * 0.58:
                    continue
                if not meta["is_vert"]:
                    # Horizontal text: furigana sits immediately above an overlapping base line
                    h_overlap = min(meta["x2"], other["x2"]) > max(meta["x1"], other["x1"])
                    v_adjacent = (other["y1"] - other["size"] * 0.7) <= meta["y1"] and meta["y2"] <= (other["y1"] + other["size"] * 0.35)
                    if h_overlap and v_adjacent:
                        is_ruby = True
                        break
                else:
                    # Vertical text: furigana sits immediately to the right of an overlapping base line
                    v_overlap = min(meta["y2"], other["y2"]) > max(meta["y1"], other["y1"])
                    h_adjacent = (other["x2"] - other["size"] * 0.35) <= meta["x1"] and meta["x2"] <= (other["x2"] + other["size"] * 0.7)
                    if v_overlap and h_adjacent:
                        is_ruby = True
                        break

            if not is_ruby:
                filtered.append(item)
        return filtered

    @staticmethod
    def _order_vertical_boxes(results):
        # Group nearby vertical columns sharing a text band. Leave other blocks
        # and horizontal UI labels in their original detector slots.
        columns = {}
        for i, item in enumerate(results):
            boxes = [c["bbox"] for c in item.get("chars", [])] if item.get("is_vertical") else []
            if boxes:
                columns[i] = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                              max(b[2] for b in boxes), max(b[3] for b in boxes))
        ordered = list(results)
        remaining = set(columns)
        while remaining:
            group = [remaining.pop()]
            for i in group:
                left, top, right, bottom = columns[i]
                for j in list(remaining):
                    other_left, other_top, other_right, other_bottom = columns[j]
                    gap = max(left - other_right, other_left - right, 0)
                    overlap = min(bottom, other_bottom) - max(top, other_top)
                    width = max(right - left, other_right - other_left)
                    height = min(bottom - top, other_bottom - other_top)
                    if gap <= 3 * width and overlap >= height / 2:
                        group.append(j)
                        remaining.remove(j)
            reading_order = sorted(group, key=lambda k: (-columns[k][0], columns[k][1], k))
            for slot, source in zip(sorted(group), reading_order):
                ordered[slot] = results[source]
        return ordered

    def _run_meiki_sync(self, image_path: str) -> str:
        """Run MeikiOCR synchronously on an image file and return joined text."""
        import numpy as np
        from PIL import Image

        with Image.open(image_path) as im:
            rgb = np.array(im.convert("RGB"))
        bgr = rgb[:, :, ::-1].copy()
        results = self._filter_furigana_boxes(meiki_ocr_engine.run_ocr(bgr) or [])
        results = self._order_vertical_boxes(results)
        lines = [
            (item.get("text") or "").strip()
            for item in results
            if (item.get("text") or "").strip()
        ]
        return "".join(lines)

    def _should_log_ocr_text(self) -> bool:
        """Return True only when OCR text preview logging is explicitly opted in."""
        return bool(self.config.get("log_ocr_text", False)) or os.environ.get("DECKY_YOMI_LOG_OCR_TEXT") == "1"

    def _log_ocr_result(self, prefix: str, text: str, regions: int | None = None):
        """Log OCR character count by default; include text snippet only when opted in."""
        meta = f"{len(text)} chars" if regions is None else f"{len(text)} chars, {regions} regions"
        if self._should_log_ocr_text():
            decky.logger.info(f"{prefix} ({meta}): {text[:80]}")
        else:
            decky.logger.info(f"{prefix} ({meta})")

    @traced
    async def run_ocr(self, image_path: str) -> str:
        """Run Japanese OCR on an image. Returns recognized text."""
        if getattr(sys, "frozen", False):
            return await self._worker_call("run_ocr", image_path)
        if meiki_ocr_engine is None and manga_ocr_engine is None:
            return "ERROR: OCR engine not loaded"

        preferred = self.config.get("ocr_engine", "meikiocr")
        loop = asyncio.get_event_loop()

        if preferred == "meikiocr" and meiki_ocr_engine is not None:
            try:
                text = await loop.run_in_executor(None, self._run_meiki_sync, image_path)
                if self.config.get("auto_copy_clipboard", False) and text:
                    try:
                        subprocess.run(["wl-copy", text], check=False)
                    except Exception:
                        pass
                self._log_ocr_result("MeikiOCR result", text)
                return text
            except Exception as e:
                decky.logger.warning(f"MeikiOCR failed ({e}); falling back to manga-ocr")
                if manga_ocr_engine is None:
                    return f"ERROR: {str(e)}"

        if manga_ocr_engine is None:
            return "ERROR: OCR engine not loaded"

        try:
            from PIL import Image, ImageEnhance
            img = Image.open(image_path).convert("RGB")

            # Split wide/multi-line captures before contrast boost so background edges don't amplify
            regions = self._split_ocr_regions(img)

            # Optional pre-processing to boost text contrast in transparent game textboxes
            if self.config.get("ocr_preprocess", True):
                enhanced_regions = []
                for r in regions:
                    try:
                        enhanced_regions.append(ImageEnhance.Contrast(r).enhance(1.3))
                    except Exception:
                        enhanced_regions.append(r)
                regions = enhanced_regions

            # Run OCR (this is CPU-bound; run in executor to not block)
            if len(regions) == 1:
                text = await loop.run_in_executor(None, manga_ocr_engine, regions[0])
            else:
                chunks = []
                for sub_img in regions:
                    chunk_text = await loop.run_in_executor(None, manga_ocr_engine, sub_img)
                    cleaned = (chunk_text or "").strip()
                    # Filter single-punctuation hallucinations on small stray boxes
                    if cleaned and not all(c in "・．.。、,，!！?？ー-―… " for c in cleaned):
                        chunks.append(cleaned)
                sep = " " if img.size[1] > 360 else ""
                if sep:
                    text = sep.join(chunks)
                else:
                    text = self._merge_ocr_chunks(chunks)

            # Automatically copy recognized sentence to clipboard if enabled
            if self.config.get("auto_copy_clipboard", False) and text:
                try:
                    subprocess.run(["wl-copy", text], check=False)
                except Exception:
                    pass

            self._log_ocr_result("OCR result", text, regions=len(regions))
            return text
        except Exception as e:
            decky.logger.error(f"OCR failed: {e}")
            return f"ERROR: {str(e)}"

    # ════════════════════════════════════════════════════════════
    #  MORPHOLOGICAL ANALYSIS
    # ════════════════════════════════════════════════════════════

    @traced
    async def segment_text(self, text: str) -> list:
        """
        Segment Japanese text into morphemes.
        Returns list of dicts: {surface, reading, lemma, pos}
        """
        if getattr(sys, "frozen", False):
            return await self._worker_call("segment_text", text)
        if morphological_tagger is None:
            # Fallback: return the whole string as one "word"
            return [{"surface": text, "reading": "", "lemma": text, "pos": "unknown"}]

        words = []
        cursor = 0
        for word in morphological_tagger(text):
            start = text.index(word.surface, cursor)
            cursor = start + len(word.surface)
            entry = {
                "surface": word.surface,
                "start": start, "end": cursor,
                "pos": str(word.pos) if word.pos else "unknown",
                "reading": "",
                "lemma": word.surface,
            }
            feat = word.feature
            if hasattr(feat, "kana") and feat.kana:
                entry["reading"] = feat.kana
            elif hasattr(feat, "pron") and feat.pron:
                entry["reading"] = feat.pron
            if hasattr(feat, "lemma") and feat.lemma:
                entry["lemma"] = feat.lemma
            entry["base"] = getattr(feat, "orthBase", None) or word.surface
            entry["base_reading"] = getattr(feat, "kanaBase", None) or entry["reading"]
            words.append(entry)

        return words

    # ════════════════════════════════════════════════════════════
    #  DICTIONARY LOOKUP
    # ════════════════════════════════════════════════════════════

    @traced
    async def lookup_word(self, word: str) -> dict:
        """Look up a word in JMDict + KANJIDIC2 and imported custom dictionaries."""
        if getattr(sys, "frozen", False):
            return await self._worker_call("lookup_word", word)
        with custom_dictionaries(Path(self.data_dir, "custom_dicts.db")) as custom_lookup:
            engine = JapaneseLookup(dictionary_engine, custom_lookup, self.config)
            return engine.lookup({"surface": word, "lemma": word, "reading": "", "pos": "unknown"})

    # ════════════════════════════════════════════════════════════
    #  FULL PIPELINE: CAPTURE → OCR → SEGMENT → LOOKUP
    # ════════════════════════════════════════════════════════════

    @traced
    async def capture_and_analyze(self, region: dict = None) -> dict:
        """
        The main entry point called from the frontend.
        Captures screen → OCR → segments → looks up every word.
        """
        if region is None:
            region = self.get_configured_region()

        # Step 1: Capture
        image_path = await self.capture_screen(region)
        if isinstance(image_path, str) and image_path.startswith("ERROR"):
            return {"error": image_path}

        # Step 2: OCR
        raw_text = await self.run_ocr(image_path)
        if raw_text.startswith("ERROR"):
            return {"error": raw_text}

        result = await self.analyze_text(raw_text, image_path)
        self.latest_result = result
        return result

    async def get_hidraw_button_state(self) -> dict:
        """Return currently pressed controller buttons (including L4/R4/L5/R5)."""
        if getattr(self, "controller_monitor", None) and self.controller_monitor.running:
            buttons = list(self.controller_monitor.get_pressed_buttons())
            return {"success": True, "buttons": buttons}
        return {"success": False, "buttons": []}

    @traced
    async def get_latest_result(self) -> dict:
        """Return the most recent analysis result."""
        return getattr(self, "latest_result", None) or {"raw_text": "", "words": []}

    @traced
    async def analyze_text(self, raw_text: str, image_path: str = "") -> dict:
        raw_text = raw_text.strip()
        if not raw_text:
            return {"error": "No text detected. Try a smaller region or enter text manually."}
        if getattr(sys, "frozen", False):
            return await self._worker_call("analyze_text", raw_text, image_path)
        words = (await self.segment_text(raw_text) if self.config.get("auto_segment", True)
                 else [{"surface": raw_text, "reading": "", "lemma": raw_text, "pos": "unknown"}])

        with custom_dictionaries(Path(self.data_dir, "custom_dicts.db")) as custom_lookup:
            engine = JapaneseLookup(dictionary_engine, custom_lookup, self.config)
            results = engine.analyze(words, self.config.get("skip_particles", True))

        return {
            "raw_text": raw_text,
            "words": results,
            "image_path": image_path,
            "timestamp": datetime.now().isoformat(),
        }

    # ════════════════════════════════════════════════════════════
    #  TRACKPAD REGION SELECTION (evdev)
    # ════════════════════════════════════════════════════════════

    @traced
    async def start_trackpad_selection(self) -> dict:
        """
        Listen to the trackpad/touchscreen for a drag gesture.
        Returns region coords mapped to screen space.
        """
        SCREEN_W = 1280
        SCREEN_H = 800
        TRACKPAD_MAX = 32767  # Default fallback range

        try:
            from evdev import InputDevice, ecodes, list_devices as evdev_list
        except ImportError:
            return {"error": "evdev not installed"}

        devices = []
        trackpad = None
        try:
            device_paths = evdev_list()
            for path in device_paths:
                try:
                    dev = InputDevice(path)
                    devices.append(dev)
                    name_lower = dev.name.lower()
                    caps = dev.capabilities()
                    # Check for explicit trackpad / touchpad
                    if "trackpad" in name_lower or "touchpad" in name_lower:
                        if "right" in name_lower:
                            trackpad = dev
                            break
                        elif trackpad is None:
                            trackpad = dev
                    # Touchscreen (e.g. FTS3528 on Steam Deck) or ABS+TOUCH device
                    elif ecodes.EV_ABS in caps and ecodes.EV_KEY in caps:
                        abs_caps = caps[ecodes.EV_ABS]
                        abs_codes = [c[0] if isinstance(c, tuple) else c for c in abs_caps]
                        key_caps = caps[ecodes.EV_KEY]
                        if ecodes.ABS_X in abs_codes and ecodes.ABS_Y in abs_codes:
                            if ecodes.BTN_TOUCH in key_caps or ecodes.BTN_LEFT in key_caps:
                                if trackpad is None:
                                    trackpad = dev
                except Exception:
                    continue

            if not trackpad:
                configured = self.get_configured_region()
                if configured:
                    decky.logger.info("start_trackpad_selection: No evdev touch device found; falling back to configured preset.")
                    return configured
                return {"error": "No trackpad or touch device found on this system. Please select a dialogue preset in Settings."}

            # Determine dynamic axis ranges
            max_x = TRACKPAD_MAX
            max_y = TRACKPAD_MAX
            try:
                absinfo_x = trackpad.absinfo(ecodes.ABS_X)
                absinfo_y = trackpad.absinfo(ecodes.ABS_Y)
                if absinfo_x and absinfo_x.max > 0:
                    max_x = absinfo_x.max
                if absinfo_y and absinfo_y.max > 0:
                    max_y = absinfo_y.max
            except Exception:
                pass

            current_x = current_y = 0
            start_x = start_y = None
            end_x = end_y = None

            # Read events with a timeout so we don't hang forever
            try:
                events = self._read_trackpad_events(trackpad)
                deadline = asyncio.get_running_loop().time() + 10.0
                while True:
                    event = await asyncio.wait_for(
                        anext(events), max(0.001, deadline - asyncio.get_running_loop().time())
                    )
                    if event is None:
                        break
                    ev_type, ev_code, ev_value = event

                    if ev_type == 3:  # EV_ABS
                        if ev_code == 0:   # ABS_X
                            current_x = ev_value
                        elif ev_code == 1:  # ABS_Y
                            current_y = ev_value
                    elif ev_type == 1:  # EV_KEY
                        if ev_code in (330, 272):  # BTN_TOUCH or BTN_LEFT
                            if ev_value == 1:
                                start_x, start_y = current_x, current_y
                            elif ev_value == 0:
                                end_x, end_y = current_x, current_y
                                break
            except asyncio.TimeoutError:
                return {"error": "Region selection timed out (10s)"}

            if start_x is not None and end_x is not None:
                region = {
                    "x": int(min(start_x, end_x) / max_x * SCREEN_W),
                    "y": int(min(start_y, end_y) / max_y * SCREEN_H),
                    "w": max(10, int(abs(end_x - start_x) / max_x * SCREEN_W)),
                    "h": max(10, int(abs(end_y - start_y) / max_y * SCREEN_H)),
                }
                decky.logger.info(f"Region selected: {region}")
                return region

            return {"error": "No complete drag gesture detected"}

        except Exception as e:
            decky.logger.error(f"Region selection failed: {e}")
            return {"error": str(e)}
        finally:
            for d in devices:
                try:
                    d.close()
                except Exception:
                    pass

    async def _read_trackpad_events(self, device):
        """Async generator yielding (type, code, value) tuples from evdev."""
        try:
            async for event in device.async_read_loop():
                yield (event.type, event.code, event.value)
        except Exception:
            yield None

    # ════════════════════════════════════════════════════════════
    #  VOCABULARY STORAGE (SQLite)
    # ════════════════════════════════════════════════════════════

    def _init_vocab_db(self):
        """Create vocabulary SQLite database if needed."""
        conn = sqlite3.connect(self.vocab_db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vocabulary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL,
                reading TEXT DEFAULT '',
                meaning TEXT DEFAULT '',
                pos TEXT DEFAULT '',
                context_sentence TEXT DEFAULT '',
                game_name TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                review_count INTEGER DEFAULT 0,
                last_reviewed TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vocab_word ON vocabulary(word)"
        )
        conn.commit()
        conn.close()
        decky.logger.info(f"Vocab DB ready at {self.vocab_db_path}")

    @traced
    async def save_word(
        self,
        word: str,
        reading: str = "",
        meaning: str = "",
        pos: str = "",
        context: str = "",
        game_name: str = "",
    ) -> dict:
        """Save a word to the vocabulary database."""
        try:
            conn = sqlite3.connect(self.vocab_db_path)
            existing = conn.execute(
                "SELECT id FROM vocabulary WHERE word = ? AND reading = ? AND meaning = ?",
                (word, reading, meaning),
            ).fetchone()
            if existing:
                conn.close()
                return {"saved": True, "id": existing[0], "duplicate": True}
            cur = conn.execute(
                """INSERT INTO vocabulary
                   (word, reading, meaning, pos, context_sentence, game_name)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (word, reading, meaning, pos, context, game_name),
            )
            word_id = cur.lastrowid
            conn.commit()
            conn.close()
            return {"saved": True, "word": word, "id": word_id}
        except Exception as e:
            decky.logger.error(f"Failed to save word '{word}': {e}")
            return {"saved": False, "error": str(e)}

    @traced
    async def get_vocab_list(self, limit: int = 50, offset: int = 0) -> list:
        """Retrieve saved vocabulary."""
        conn = sqlite3.connect(self.vocab_db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM vocabulary ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    @traced
    async def get_vocab_count(self) -> int:
        """Return total vocabulary count."""
        conn = sqlite3.connect(self.vocab_db_path)
        count = conn.execute("SELECT COUNT(*) FROM vocabulary").fetchone()[0]
        conn.close()
        return count

    @traced
    async def delete_word(self, word_id: int) -> dict:
        """Delete a word from vocabulary."""
        conn = sqlite3.connect(self.vocab_db_path)
        conn.execute("DELETE FROM vocabulary WHERE id = ?", (word_id,))
        conn.commit()
        conn.close()
        return {"deleted": True, "id": word_id}

    @traced
    async def export_vocab_csv(self, spreadsheet_safe: bool | None = None) -> str:
        """Export vocabulary to TSV (Anki-compatible by default, or spreadsheet-safe)."""
        import csv

        safe = bool(self.config.get("spreadsheet_safe_export", False)) if spreadsheet_safe is None else bool(spreadsheet_safe)

        conn = sqlite3.connect(self.vocab_db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM vocabulary ORDER BY created_at ASC, id ASC"
        ).fetchall()
        conn.close()

        filename = "vocab_export_safe.tsv" if safe else "vocab_export.tsv"
        output_path = os.path.join(self.data_dir, filename)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="\t")  # TSV for Anki
            writer.writerow([
                "word", "reading", "meaning", "pos",
                "context", "game", "created_at",
            ])
            for row in rows:
                fields = [
                    row["word"], row["reading"], row["meaning"],
                    row["pos"], row["context_sentence"],
                    row["game_name"], row["created_at"],
                ]
                if safe:
                    fields = [_sanitize_tsv_cell(val) for val in fields]
                writer.writerow(fields)

        decky.logger.info(f"Vocab exported → {output_path} ({len(rows)} words, safe={safe})")
        return output_path

    # ════════════════════════════════════════════════════════════
    #  YOMITAN DICTIONARY IMPORT
    # ════════════════════════════════════════════════════════════

    def _import_yomitan_dict_sync(self, zip_path: str) -> dict:
        """Synchronous bounded Yomitan .zip importer (run off the asyncio event loop)."""
        if not os.path.isfile(zip_path):
            raise FileNotFoundError(f"Dictionary archive not found: {zip_path}")
        archive_size = os.path.getsize(zip_path)
        if archive_size > YOMITAN_MAX_ARCHIVE_BYTES:
            raise ValueError(f"Dictionary archive exceeds {YOMITAN_MAX_ARCHIVE_BYTES} byte limit")

        custom_db_path = os.path.join(self.data_dir, "custom_dicts.db")
        total_bytes = 0
        count = 0
        conn = None

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                infos = zf.infolist()
                if len(infos) > YOMITAN_MAX_ZIP_ENTRIES:
                    raise ValueError(f"Dictionary archive has {len(infos)} entries (max {YOMITAN_MAX_ZIP_ENTRIES})")

                info_by_name = {info.filename: info for info in infos}
                if "index.json" not in info_by_name:
                    raise ValueError("Missing index.json in dictionary archive")

                index_bytes = _read_zip_entry_limited(zf, info_by_name["index.json"], YOMITAN_MAX_INDEX_BYTES)
                total_bytes += len(index_bytes)
                index_data = json.loads(index_bytes)
                if not isinstance(index_data, dict):
                    raise ValueError("Invalid index.json format")

                dict_title = str(index_data.get("title", "Unknown"))[:128]
                safe_name = "".join(
                    c if c.isalnum() or c == "_" else "_"
                    for c in dict_title.lower()
                )[:32] or "custom"
                table = f"dict_{safe_name}"

                conn = sqlite3.connect(custom_db_path)
                conn.execute("BEGIN")
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS [{table}] (
                        expression TEXT,
                        reading TEXT,
                        tags TEXT,
                        rules TEXT,
                        score INTEGER,
                        glossary TEXT,
                        sequence INTEGER,
                        extra TEXT
                    )
                """)
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{safe_name}_expr "
                    f"ON [{table}](expression)"
                )

                conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{safe_name}_reading ON [{table}](reading)")
                ignored_banks = set()
                for info in infos:
                    if not (info.filename.startswith("term_bank_") and info.filename.endswith(".json")):
                        if "_bank_" in info.filename:
                            ignored_banks.add(info.filename.split("_bank_")[0])
                        continue
                    remaining = YOMITAN_MAX_TOTAL_BYTES - total_bytes
                    if remaining <= 0:
                        raise ValueError(f"Total uncompressed size exceeds {YOMITAN_MAX_TOTAL_BYTES} byte limit")
                    raw_entry = _read_zip_entry_limited(zf, info, min(YOMITAN_MAX_ENTRY_BYTES, remaining))
                    total_bytes += len(raw_entry)

                    terms = json.loads(raw_entry)
                    if not isinstance(terms, list):
                        raise ValueError(f"Invalid term bank format in '{info.filename}'")

                    batch = []
                    for t in terms:
                        if count >= YOMITAN_MAX_TERMS:
                            raise ValueError(f"Dictionary exceeds {YOMITAN_MAX_TERMS} term limit")
                        if not isinstance(t, list) or len(t) < 7:
                            raise ValueError(f"Malformed term entry in '{info.filename}'")

                        expr = str(t[0])
                        reading = str(t[1])
                        tags = str(t[2])
                        rules = str(t[3])
                        extra = str(t[7]) if len(t) > 7 and t[7] is not None else ""
                        for field_name, val in (
                            ("expression", expr), ("reading", reading),
                            ("tags", tags), ("rules", rules), ("extra", extra),
                        ):
                            if len(val) > YOMITAN_MAX_FIELD_CHARS:
                                raise ValueError(
                                    f"Field '{field_name}' exceeds {YOMITAN_MAX_FIELD_CHARS} char limit"
                                )

                        glossary_json = json.dumps(t[5], ensure_ascii=False)
                        if len(glossary_json) > YOMITAN_MAX_GLOSSARY_CHARS:
                            raise ValueError(
                                f"Glossary entry exceeds {YOMITAN_MAX_GLOSSARY_CHARS} char limit"
                            )

                        score = int(t[4]) if isinstance(t[4], (int, float)) else 0
                        sequence = int(t[6]) if isinstance(t[6], (int, float)) else 0
                        batch.append((expr, reading, tags, rules, score, glossary_json, sequence, extra))
                        count += 1

                    if batch:
                        conn.executemany(
                            f"INSERT INTO [{table}] VALUES (?,?,?,?,?,?,?,?)",
                            batch,
                        )

            if count == 0:
                raise ValueError("No terms to import. Supported bank type: term_bank. Found: "
                                 + (", ".join(sorted(ignored_banks)) or "no term banks"))
            if conn is not None:
                conn.commit()
            decky.logger.info(f"Imported Yomitan dict '{dict_title}': {count} terms")
            return {"imported": True, "title": dict_title, "terms": count, "ignored_banks": sorted(ignored_banks)}
        except Exception:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            if conn is not None:
                conn.close()

    @traced
    async def import_yomitan_dict(self, zip_path: str) -> dict:
        """Import a Yomitan-format .zip dictionary into SQLite off the event loop."""
        try:
            return await asyncio.to_thread(self._import_yomitan_dict_sync, zip_path)
        except Exception as e:
            decky.logger.error(f"Yomitan import failed: {e}")
            return {"imported": False, "error": str(e)}

    @traced
    async def lookup_custom_dict(self, word: str) -> list:
        """Look up a word across all imported Yomitan dictionaries."""
        with custom_dictionaries(Path(self.data_dir, "custom_dicts.db")) as lookup:
            return lookup(word, limit=5)

    # ════════════════════════════════════════════════════════════
    #  SETTINGS
    # ════════════════════════════════════════════════════════════

    def _load_config(self):
        """Load settings from config.json, merging with defaults."""
        defaults_path = os.path.join(PLUGIN_DIR, "defaults", "config.json")
        defaults = {}
        if os.path.exists(defaults_path):
            with open(defaults_path, "r") as f:
                defaults = json.load(f)

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    saved = json.load(f)
                defaults.update(saved)
            except Exception as e:
                decky.logger.error(f"Failed to load config: {e}")

        self.config = defaults
        decky.logger.info(f"Config loaded: {len(self.config)} keys")

    @traced
    async def get_settings(self) -> dict:
        """Return current settings."""
        return self.config

    @traced
    async def update_settings(self, settings: dict) -> dict:
        """Update and persist settings."""
        self.config.update(settings)
        try:
            with open(self.config_path, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            decky.logger.error(f"Failed to save config: {e}")
            raise
        return self.config
