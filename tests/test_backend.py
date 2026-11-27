"""
Automated unit tests for Decky Yomi backend (Plugin class).
Tests lifecycle, settings, vocabulary database, export, and custom dictionary logic.
"""
import asyncio
import os
import shutil
import tempfile
import unittest
import zipfile
import json
from main import Plugin, PLUGIN_DIR

class TestDeckyYomiBackend(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()
        # Set a temporary directory for test isolated state
        self.test_dir = tempfile.mkdtemp(prefix="decky_yomi_test_")
        self.plugin.data_dir = self.test_dir
        self.plugin.config_path = os.path.join(self.test_dir, "config.json")
        self.plugin.vocab_db_path = os.path.join(self.test_dir, "vocab.db")
        self.plugin._load_config()
        self.plugin._init_vocab_db()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_duplicate_save_is_idempotent(self):
        async def run():
            first = await self.plugin.save_word("猫", "ねこ", "cat")
            second = await self.plugin.save_word("猫", "ねこ", "cat")
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(await self.plugin.get_vocab_count(), 1)
        asyncio.run(run())

    def test_manual_analysis_and_segmentation_setting(self):
        async def run():
            self.assertIn("error", await self.plugin.analyze_text("   "))
            self.plugin.config["auto_segment"] = False
            result = await self.plugin.analyze_text(" 日本語 ")
            self.assertEqual(result["raw_text"], "日本語")
            self.assertEqual(len(result["words"]), 1)
        asyncio.run(run())

    def test_diagnostics_record_calls_and_bound_frontend_batches(self):
        from unittest.mock import Mock
        logger = Mock()
        self.plugin.diagnostic_logger = logger
        asyncio.run(self.plugin.get_settings())
        events = [json.loads(call.args[0]) for call in logger.info.call_args_list]
        self.assertEqual([e["event"] for e in events], ["start", "end"])
        self.assertEqual(events[0]["request"], events[1]["request"])
        logger.reset_mock()
        asyncio.run(self.plugin.log_frontend(["x" * 7000] * 30))
        self.assertEqual(logger.info.call_count, 25)
        self.assertEqual(len(json.loads(logger.info.call_args.args[0])["message"]), 6000)

    def test_installer_never_reexecutes_frozen_loader(self):
        from unittest.mock import patch
        with patch("main.sys.executable", "/home/deck/homebrew/services/PluginLoader"), patch.dict(os.environ, {"PYTHONHOME": "/tmp/frozen", "LD_LIBRARY_PATH": "/tmp/frozen", "LD_LIBRARY_PATH_ORIG": "/usr/lib"}):
            python, env = self.plugin._installer_runtime()
        self.assertEqual(python, "/usr/bin/python3")
        self.assertNotIn("PYTHONHOME", env)
        self.assertEqual(env["LD_LIBRARY_PATH"], "/usr/lib")

    def test_private_installer_bootstraps_pip(self):
        async def run():
            self.plugin.install_state = {}
            python, env = await self.plugin._prepare_installer_runtime()
            self.assertTrue(python.startswith(self.test_dir))
            self.assertEqual(env["TMPDIR"], os.path.join(self.test_dir, "installer-tmp"))
            self.assertTrue(os.path.isdir(env["TMPDIR"]))
            self.assertTrue(os.path.isfile(python))
            await self.plugin._verify_installer_runtime(python, env)
        asyncio.run(run())

    def test_settings_load_and_update(self):
        loop = asyncio.new_event_loop()
        settings = loop.run_until_complete(self.plugin.get_settings())
        self.assertIn("ocr_engine", settings)
        self.assertIn("auto_segment", settings)
        self.assertEqual(settings.get("ocr_engine"), "meikiocr")

        # Update settings
        updated = loop.run_until_complete(self.plugin.update_settings({
            "ocr_engine": "manga_ocr",
            "max_results": 8
        }))
        self.assertEqual(updated["ocr_engine"], "manga_ocr")
        self.assertEqual(updated["max_results"], 8)

        # Verify persisted on disk
        with open(self.plugin.config_path, "r") as f:
            disk_data = json.load(f)
        self.assertEqual(disk_data["ocr_engine"], "manga_ocr")
        self.assertEqual(disk_data["max_results"], 8)
        loop.close()

    def test_vocab_crud_and_export(self):
        loop = asyncio.new_event_loop()

        # Check initial count is 0
        count0 = loop.run_until_complete(self.plugin.get_vocab_count())
        self.assertEqual(count0, 0)

        # Save word 1
        res1 = loop.run_until_complete(self.plugin.save_word(
            word="食べる",
            reading="たべる",
            meaning="to eat",
            pos="動詞",
            context="リンゴを食べる。",
            game_name="Persona 4 Golden"
        ))
        self.assertTrue(res1.get("saved"))
        word1_id = res1["id"]

        # Save word 2
        res2 = loop.run_until_complete(self.plugin.save_word(
            word="冒険",
            reading="ぼうけん",
            meaning="adventure",
            pos="名詞",
            context="冒険が始まる！",
            game_name="Dragon Quest XI"
        ))
        self.assertTrue(res2.get("saved"))
        word2_id = res2["id"]

        # Check count is 2
        count2 = loop.run_until_complete(self.plugin.get_vocab_count())
        self.assertEqual(count2, 2)

        # Check list retrieval
        vocab_list = loop.run_until_complete(self.plugin.get_vocab_list(limit=10))
        self.assertEqual(len(vocab_list), 2)
        self.assertEqual(vocab_list[0]["word"], "冒険") # order by created_at desc
        self.assertEqual(vocab_list[1]["word"], "食べる")

        # Export to CSV / TSV
        csv_path = loop.run_until_complete(self.plugin.export_vocab_csv())
        self.assertTrue(os.path.exists(csv_path))
        with open(csv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 3) # Header + 2 rows
        self.assertIn("食べる", lines[1])
        self.assertIn("冒険", lines[2])

        # Delete word
        del_res = loop.run_until_complete(self.plugin.delete_word(word1_id))
        self.assertTrue(del_res.get("deleted"))

        count_after = loop.run_until_complete(self.plugin.get_vocab_count())
        self.assertEqual(count_after, 1)

        loop.close()

    def test_yomitan_dictionary_import(self):
        loop = asyncio.new_event_loop()

        # Create a mock Yomitan zip dictionary in memory
        zip_path = os.path.join(self.test_dir, "test_dict.zip")
        index_data = {
            "title": "Test J-E Dict",
            "revision": "1.0",
            "format": 3,
            "author": "Tester",
            "description": "Test dictionary for Decky Yomi"
        }
        term_bank = [
            ["日本語", "にほんご", "n", "", 10, ["Japanese language"], 100, ""],
            ["勉強", "べんきょう", "vs n", "", 9, ["study", "diligence"], 101, ""]
        ]

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("index.json", json.dumps(index_data))
            zf.writestr("term_bank_1.json", json.dumps(term_bank))

        # Import the zip
        import_res = loop.run_until_complete(self.plugin.import_yomitan_dict(zip_path))
        self.assertTrue(import_res.get("imported"))
        self.assertEqual(import_res.get("terms"), 2)

        # Lookup from imported dict via direct helper
        lookup_res = loop.run_until_complete(self.plugin.lookup_custom_dict("日本語"))
        self.assertEqual(len(lookup_res), 1)
        self.assertEqual(lookup_res[0]["expression"], "日本語")
        self.assertEqual(lookup_res[0]["reading"], "にほんご")
        self.assertIn("Japanese language", lookup_res[0]["glossary"])

        # Lookup from imported dict through main lookup_word pipeline
        word_lookup = loop.run_until_complete(self.plugin.lookup_word("日本語"))
        self.assertEqual(word_lookup["query"], "日本語")
        self.assertGreaterEqual(len(word_lookup["entries"]), 1)
        self.assertEqual(word_lookup["entries"][0]["kanji"], ["日本語"])
        self.assertEqual(word_lookup["entries"][0]["kana"], ["にほんご"])

        loop.close()

    def test_pipeline_fallbacks_without_engines(self):
        """Test that methods return graceful fallbacks when heavy external ML/C libraries aren't loaded."""
        loop = asyncio.new_event_loop()

        # Engine status
        status = loop.run_until_complete(self.plugin.get_engine_status())
        self.assertIn("ocr", status)
        self.assertIn("segmentation", status)
        self.assertIn("dictionary", status)

        # Segmentation fallback
        segmented = loop.run_until_complete(self.plugin.segment_text("日本語"))
        self.assertEqual(len(segmented), 1)
        self.assertEqual(segmented[0]["surface"], "日本語")

        # Dictionary lookup fallback
        lookup = loop.run_until_complete(self.plugin.lookup_word("猫"))
        self.assertEqual(lookup["query"], "猫")
        self.assertIn("error", lookup)

        # OCR fallback
        ocr_res = loop.run_until_complete(self.plugin.run_ocr("/dummy/path.png"))
        self.assertTrue(ocr_res.startswith("ERROR:"))

        loop.close()

    def test_in_ui_component_status_and_installer_structure(self):
        """Test the in-UI component status, packaging definitions, and install state tracking."""
        loop = asyncio.new_event_loop()

        # Check components status
        comp_status = loop.run_until_complete(self.plugin.get_components_status())
        self.assertIn("components", comp_status)
        self.assertIn("all_installed", comp_status)
        self.assertIn("installing", comp_status)
        self.assertEqual(len(comp_status["components"]), 4)

        # Check component IDs
        comp_ids = [c["id"] for c in comp_status["components"]]
        self.assertIn("dictionary", comp_ids)
        self.assertIn("segmentation", comp_ids)
        self.assertIn("ocr", comp_ids)
        self.assertIn("input", comp_ids)

        # Verify initial progress state
        progress = loop.run_until_complete(self.plugin.get_install_progress())
        self.assertEqual(progress["status"], "idle")
        self.assertEqual(progress["percent"], 0)

        # Verify cancel when none running
        cancel_res = loop.run_until_complete(self.plugin.cancel_install())
        self.assertFalse(cancel_res["cancelled"])

        loop.close()

    def test_torch_requirement_arch_selection(self):
        from main import _torch_requirement
        x86_spec, x86_extra = _torch_requirement("x86_64", "3.14")
        self.assertIn("manylinux_2_28_x86_64.whl", x86_spec)
        self.assertEqual(x86_extra, [])

        arm_spec, arm_extra = _torch_requirement("aarch64", "3.14")
        self.assertIn("manylinux_2_28_aarch64.whl", arm_spec)
        self.assertEqual(arm_extra, [])

        mac_arm_spec, _ = _torch_requirement("arm64", "3.14")
        self.assertEqual(mac_arm_spec, arm_spec)

        fb_spec = None
        with self.assertRaises(RuntimeError):
            _torch_requirement("aarch64", "3.12")
        self.assertIsNone(fb_spec)

    def test_get_pip_checksum_verification_and_locked_requirements(self):
        import io
        from unittest.mock import patch
        import main

        good = b"print('ok')"
        bad_resp = io.BytesIO(b"tampered")
        dest = os.path.join(self.test_dir, "get-pip.py")
        with patch("main.urllib.request.urlopen", return_value=bad_resp):
            with self.assertRaises(ValueError):
                self.plugin._download_get_pip(dest)
        self.assertFalse(os.path.exists(dest))

        good_digest = main.hashlib.sha256(good).hexdigest()
        with patch("main.GET_PIP_SHA256", good_digest), \
             patch("main.urllib.request.urlopen", return_value=io.BytesIO(good)) as mock_urlopen:
            self.plugin._download_get_pip(dest)
        self.assertTrue(os.path.exists(dest))
        import ssl
        self.assertIsInstance(mock_urlopen.call_args.kwargs.get("context"), ssl.SSLContext)

        locked_out = os.path.join(self.test_dir, "locked.txt")
        self.plugin._write_locked_requirements(["dictionary"], locked_out)
        with open(locked_out, "r", encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("jamdict==0.1a11.post2", txt)
        self.assertIn("--hash=sha256:", txt)
        self.assertNotIn("--extra-index-url", txt)

    def test_yomitan_import_rejects_oversized_glossary_and_zip_limits(self):
        from unittest.mock import patch
        async def run():
            # Reject a 2 MB glossary inside a tiny compressed archive
            bomb_zip = os.path.join(self.test_dir, "bomb.zip")
            with zipfile.ZipFile(bomb_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("index.json", json.dumps({"title": "Bomb Dict"}))
                zf.writestr("term_bank_1.json", json.dumps([
                    ["猫", "ねこ", "n", "", 1, ["x" * (2 * 1024 * 1024)], 1, ""]
                ]))
            res = await self.plugin.import_yomitan_dict(bomb_zip)
            self.assertFalse(res.get("imported"))
            self.assertIn("limit", res.get("error", "").lower())

            # Reject archives exceeding entry count cap
            many_zip = os.path.join(self.test_dir, "many.zip")
            with zipfile.ZipFile(many_zip, "w") as zf:
                zf.writestr("index.json", json.dumps({"title": "Many Dict"}))
                for idx in range(255):
                    zf.writestr(f"extra_{idx}.txt", "x")
            res_many = await self.plugin.import_yomitan_dict(many_zip)
            self.assertFalse(res_many.get("imported"))

            # Verify import runs via asyncio.to_thread (off event loop)
            good_zip = os.path.join(self.test_dir, "good.zip")
            with zipfile.ZipFile(good_zip, "w") as zf:
                zf.writestr("index.json", json.dumps({"title": "Thread Dict"}))
                zf.writestr("term_bank_1.json", json.dumps([["犬", "いぬ", "n", "", 1, ["dog"], 1, ""]]))
            calls = []
            orig_to_thread = asyncio.to_thread
            async def spy_to_thread(fn, *a, **kw):
                calls.append(fn.__name__)
                return await orig_to_thread(fn, *a, **kw)
            with patch("main.asyncio.to_thread", side_effect=spy_to_thread):
                ok = await self.plugin.import_yomitan_dict(good_zip)
            self.assertTrue(ok.get("imported"))
            self.assertEqual(calls, ["_import_yomitan_dict_sync"])
        asyncio.run(run())

    def test_ocr_text_logging_is_opt_in(self):
        from unittest.mock import patch
        import main

        secret_text = "秘密のパスワードTEXT"
        with patch.object(main.decky.logger, "info") as mock_info:
            self.plugin.config["log_ocr_text"] = False
            self.plugin._log_ocr_result("MeikiOCR result", secret_text)
            self.plugin._log_ocr_result("OCR result", secret_text, regions=1)
            logged_default = " ".join(c.args[0] for c in mock_info.call_args_list)
            self.assertIn("chars", logged_default)
            self.assertNotIn(secret_text, logged_default)

            mock_info.reset_mock()
            self.plugin.config["log_ocr_text"] = True
            self.plugin._log_ocr_result("MeikiOCR result", secret_text)
            logged_opt_in = mock_info.call_args_list[0].args[0]
            self.assertIn(secret_text, logged_opt_in)

    def test_vocab_export_spreadsheet_safe_option(self):
        async def run():
            await self.plugin.save_word("=1+1", "+cmd", "-minus", "@pos", "=HYPERLINK()", "game")
            anki_path = await self.plugin.export_vocab_csv()
            with open(anki_path, "r", encoding="utf-8") as f:
                anki_txt = f.read()
            self.assertIn("=1+1\t+cmd\t-minus\t@pos\t=HYPERLINK()", anki_txt)

            safe_path = await self.plugin.export_vocab_csv(spreadsheet_safe=True)
            with open(safe_path, "r", encoding="utf-8") as f:
                safe_txt = f.read()
            self.assertIn("'=1+1\t'+cmd\t'-minus\t'@pos\t'=HYPERLINK()", safe_txt)
        asyncio.run(run())

    def test_session_cmd_skips_runuser_when_deck_missing(self):
        from unittest.mock import patch
        import pwd
        with patch("main.os.geteuid", return_value=0), patch("main.shutil.which", return_value="/usr/sbin/runuser"), patch.object(pwd, "getpwnam", side_effect=KeyError("deck")):
            cmd, env = self.plugin._session_cmd_and_env(["ffmpeg", "-vframes", "1"], display=":0")
        self.assertEqual(cmd, ["ffmpeg", "-vframes", "1"])
        self.assertEqual(env["DISPLAY"], ":0")

    def test_start_install_skips_unbuildable_evdev_in_all(self):
        from unittest.mock import patch
        async def run():
            with patch.object(self.plugin, "_is_module_available", return_value=False), \
                 patch.object(self.plugin, "_can_build_evdev", return_value=False), \
                 patch("main.asyncio.create_task") as mock_task:
                res = await self.plugin.start_install("all")
                self.assertTrue(res["started"])
                mock_task.assert_called_once()
                scheduled = mock_task.call_args.args[0]
                scheduled.close()
                self.assertNotIn("evdev", self.plugin.install_state["logs"][1])
                self.plugin.install_state["status"] = "idle"
                res_input = await self.plugin.start_install("input")
                self.assertFalse(res_input["started"])
                self.assertIn("evdev", res_input["error"])
        asyncio.run(run())

    def test_screen_capture_and_display_detection(self):
        """Test display detection and screen capture execution."""
        loop = asyncio.new_event_loop()

        # Display detection should return string starting with ':'
        disp = self.plugin._get_x11_display()
        self.assertTrue(disp.startswith(":"))

        # Screen capture via ffmpeg
        capture_path = loop.run_until_complete(self.plugin.capture_screen())
        self.assertFalse(capture_path.startswith("ERROR:"), f"Capture returned error: {capture_path}")
        self.assertTrue(os.path.exists(capture_path))
        self.assertGreater(os.path.getsize(capture_path), 0)

        loop.close()

    def test_controller_button_state_and_latest_result(self):
        """Test button state RPC and latest result storage."""
        loop = asyncio.new_event_loop()

        # Button state RPC
        res = loop.run_until_complete(self.plugin.get_hidraw_button_state())
        self.assertIn("success", res)
        self.assertIn("buttons", res)
        self.assertIsInstance(res["buttons"], list)

        # Latest result storage
        initial_result = loop.run_until_complete(self.plugin.get_latest_result())
        self.assertIn("raw_text", initial_result)

        loop.close()

    def test_controller_monitor_reads_droiddeck_rbif_ring(self):
        import struct
        from unittest.mock import patch
        from backend.controller_monitor import ControllerButtonMonitor, RBIF_IMU_OFFSET

        ring_path = os.path.join(self.test_dir, "ring0")
        buf = bytearray(0x18180)
        struct.pack_into("<4sIII", buf, 0, b"RBIF", 2, 24, 4096)
        struct.pack_into("<Q", buf, 0x20, 4)  # even seqlock
        # Base buttons: A (0x001) + SELECT (0x040), triggers: R2=200, L2=0, dpad: UP (-1)
        struct.pack_into("<I", buf, 0x28, 0x041)
        struct.pack_into("<4h", buf, 0x34, 200, 0, 0, -1)
        # IMU1 tail with L4 (0x01) + R5 (0x08)
        struct.pack_into("<4s4xQ", buf, RBIF_IMU_OFFSET, b"IMU1", 2)
        struct.pack_into("<I", buf, RBIF_IMU_OFFSET + 0x28, 0x09)
        with open(ring_path, "wb") as f:
            f.write(buf)

        mon = ControllerButtonMonitor()
        with patch.object(mon, "find_hidraw_device", return_value=None), \
             patch.dict(os.environ, {"FAKE_EVDEV_MEMFD_PATHS": f"0={ring_path}"}):
            started = mon.start()
            self.assertTrue(started)
            try:
                buttons = mon._sample_ring_buttons()
                self.assertEqual(buttons, {"A", "SELECT", "R2", "DPAD_UP", "L4", "R5"})
            finally:
                mon.stop()

if __name__ == "__main__":
    unittest.main()
