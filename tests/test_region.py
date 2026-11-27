"""
Unit tests for region selection presets and custom crop geometry.
"""
import asyncio
import os
import shutil
import tempfile
import unittest
from main import Plugin

class TestRegionSelection(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()
        self.test_dir = tempfile.mkdtemp(prefix="decky_yomi_region_test_")
        self.plugin.data_dir = self.test_dir
        self.plugin.config_path = os.path.join(self.test_dir, "config.json")
        self.plugin._load_config()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_configured_region_presets(self):
        # 1. Fullscreen -> None
        self.plugin.config["ocr_mode"] = "fullscreen"
        self.assertIsNone(self.plugin.get_configured_region())

        # 2. Bottom 30%
        self.plugin.config["ocr_mode"] = "bottom_30"
        r30 = self.plugin.get_configured_region()
        self.assertEqual(r30, {"x": 0, "y": 70, "w": 100, "h": 30, "unit": "percent"})

        # 3. Bottom 40%
        self.plugin.config["ocr_mode"] = "bottom_40"
        r40 = self.plugin.get_configured_region()
        self.assertEqual(r40, {"x": 0, "y": 60, "w": 100, "h": 40, "unit": "percent"})

        # 4. Center
        self.plugin.config["ocr_mode"] = "center"
        r_center = self.plugin.get_configured_region()
        self.assertEqual(r_center, {"x": 15, "y": 25, "w": 70, "h": 50, "unit": "percent"})

        # 5. Legacy "region"
        self.plugin.config["ocr_mode"] = "region"
        r_legacy = self.plugin.get_configured_region()
        self.assertEqual(r_legacy, {"x": 0, "y": 70, "w": 100, "h": 30, "unit": "percent"})

        # 6. Custom region
        self.plugin.config["ocr_mode"] = "custom"
        self.plugin.config["custom_region_x"] = 10
        self.plugin.config["custom_region_y"] = 65
        self.plugin.config["custom_region_w"] = 80
        self.plugin.config["custom_region_h"] = 25
        r_custom = self.plugin.get_configured_region()
        self.assertEqual(r_custom, {"x": 10.0, "y": 65.0, "w": 80.0, "h": 25.0, "unit": "percent"})

    def test_crop_image_percentage(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("PIL not available in test runner")

        img_path = os.path.join(self.test_dir, "test_1080p.png")
        img = Image.new("RGB", (1920, 1080), color=(100, 150, 200))
        img.save(img_path)

        # Crop bottom 30%: y = 70% of 1080 = 756, h = 30% of 1080 = 324
        region = {"x": 0, "y": 70, "w": 100, "h": 30, "unit": "percent"}
        self.plugin._crop_image(img_path, region)

        with Image.open(img_path) as cropped:
            self.assertEqual(cropped.size, (1920, 324))

    def test_merge_ocr_chunks_overlap(self):
        chunks = [
            "でも今のような真冬では、線どころ",
            "ろか薪の下に立っているような気",
            "気がする。",
        ]
        merged = self.plugin._merge_ocr_chunks(chunks)
        self.assertEqual(merged, "でも今のような真冬では、線どころか薪の下に立っているような気がする。")

    def test_split_ocr_regions_wide_image(self):
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self.skipTest("PIL not available in test runner")

        img = Image.new("RGB", (1920, 324), color=(20, 20, 20))
        draw = ImageDraw.Draw(img)
        draw.rectangle((100, 80, 1400, 130), fill=(240, 240, 240))
        regions = self.plugin._split_ocr_regions(img)
        self.assertGreater(len(regions), 1)
        for r in regions:
            rw, rh = r.size
            self.assertLessEqual(rw, rh * 4.0)

    def test_detect_text_boxes_2d_fullscreen(self):
        try:
            import numpy as np  # noqa: F401
            from PIL import Image, ImageDraw
        except ImportError:
            self.skipTest("PIL/numpy not available in test runner")

        img = Image.new("RGB", (1280, 800), color=(240, 225, 190))
        draw = ImageDraw.Draw(img)
        # Simulated menu/dialogue blocks
        draw.rectangle((220, 515, 360, 548), fill=(40, 40, 40))
        draw.rectangle((220, 640, 340, 670), fill=(40, 40, 40))
        regions = self.plugin._split_ocr_regions(img)
        self.assertEqual(len(regions), 2)

    def test_meiki_ocr_engine_dispatch(self):
        try:
            import numpy as np  # noqa: F401
            from PIL import Image
        except ImportError:
            self.skipTest("PIL/numpy not available in test runner")

        import main as main_mod

        img_path = os.path.join(self.test_dir, "sample.png")
        Image.new("RGB", (320, 120), color=(0, 0, 0)).save(img_path)

        class FakeMeiki:
            def run_ocr(self, bgr):
                return [{"text": "一行目"}, {"text": "二行目"}]

        old_meiki = main_mod.meiki_ocr_engine
        main_mod.meiki_ocr_engine = FakeMeiki()
        try:
            self.plugin.config["ocr_engine"] = "meikiocr"
            result = asyncio.run(self.plugin.run_ocr(img_path))
            self.assertEqual(result, "一行目二行目")
        finally:
            main_mod.meiki_ocr_engine = old_meiki

    def test_filter_furigana_boxes_general(self):
        from main import Plugin

        def make_box(text, x1, y1, h, w_per_char=20):
            chars = []
            for i, ch in enumerate(text):
                cx1 = x1 + i * w_per_char
                chars.append({"char": ch, "bbox": [cx1, y1, cx1 + w_per_char, y1 + h], "conf": 0.9})
            return {"text": text, "chars": chars, "is_vertical": False}

        # 1. Adjacent kana-only furigana above larger dialogue is filtered
        boxes = [
            make_box("ちか", 200, 88, 9, 10),
            make_box("マークに近づいて", 140, 93, 29, 26),
            make_box("はな", 160, 127, 10, 10),
            make_box("で話しかけよう!", 140, 135, 26, 26),
        ]
        filtered = Plugin._filter_furigana_boxes(boxes)
        self.assertEqual([b["text"] for b in filtered], ["マークに近づいて", "で話しかけよう!"])

        # 2. Distant smaller kana menu text below a giant title logo is preserved
        title_boxes = [
            make_box("白き聖灰の女王", 300, 100, 70, 60),
            make_box("はじめから", 450, 460, 24, 24),
        ]
        filtered_title = Plugin._filter_furigana_boxes(title_boxes)
        self.assertEqual([b["text"] for b in filtered_title], ["白き聖灰の女王", "はじめから"])

        # 3. Small kanji label above larger line is preserved (not kana-only)
        label_boxes = [
            make_box("教室", 200, 85, 11, 12),
            make_box("マークに近づいて", 140, 93, 29, 26),
        ]
        filtered_label = Plugin._filter_furigana_boxes(label_boxes)
        self.assertEqual([b["text"] for b in filtered_label], ["教室", "マークに近づいて"])

    def test_filter_background_noise_drops_photo_signs_and_keeps_dialogue(self):
        from main import Plugin

        def make_box(text, y, h, conf):
            return {
                "text": text,
                "is_vertical": False,
                "chars": [
                    {"bbox": [100 + i * 20, y, 118 + i * 20, y + h], "conf": conf}
                    for i in range(max(1, len(text)))
                ],
            }

        boxes = [
            make_box("SOUEN", 20, 28, 0.43),
            make_box("渋谷のスクランブル交差点は、今日も切れ目の", 71, 57, 0.91),
            make_box("ない雑踏で覆いつくされている。", 152, 56, 0.95),
            make_box("しかし、道行く人々の誰もが気づいてはいなかっ", 230, 61, 0.91),
            make_box("た。", 316, 49, 0.90),
            make_box("サロンバプ", 332, 19, 0.75),
            make_box("AYA", 450, 10, 0.88),
            make_box("GRRAK", 481, 12, 0.39),
        ]
        filtered = Plugin._filter_background_noise(boxes)
        self.assertEqual(
            [b["text"] for b in filtered],
            [
                "渋谷のスクランブル交差点は、今日も切れ目の",
                "ない雑踏で覆いつくされている。",
                "しかし、道行く人々の誰もが気づいてはいなかっ",
                "た。",
            ],
        )

        english_only = [make_box("START", 100, 30, 0.9), make_box("OPTIONS", 150, 30, 0.9)]
        self.assertEqual(len(Plugin._filter_background_noise(english_only)), 2)
