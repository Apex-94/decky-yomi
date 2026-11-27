import asyncio
import csv
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import zipfile

import main
from backend.japanese import jmdict_entries


class JapaneseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.plugin = main.Plugin()
        self.plugin.data_dir = directory.name
        self.plugin.config = {"max_results": 5}
        self.plugin.vocab_db_path = str(Path(directory.name, "vocab.db"))
        self.plugin._init_vocab_db()

    def use_japanese_engines(self):
        try:
            from fugashi import Tagger
            from jamdict import Jamdict
            dictionary = Jamdict()
            if not dictionary.is_available():
                self.skipTest("Jamdict data unavailable")
            tagger = Tagger()
        except ImportError:
            self.skipTest("Install Japanese engine packages to run integration tests")
        self.enterContext(patch.object(main, "dictionary_engine", dictionary))
        self.enterContext(patch.object(main, "morphological_tagger", tagger))

    def test_sentences_and_source_offsets(self):
        self.use_japanese_engines()
        async def check():
            for sentence, surface, reading, head, meaning in [
                ("彼はもう帰っちゃった。", "彼", "かれ", "彼", "he"),
                ("彼はもう帰っちゃった。", "帰っちゃった", "かえる", "帰る", "to return"),
                ("気をつけて。", "気をつけて", "きをつける", "気をつける", "to be careful"),
                ("学校に行った。", "行った", "いく", "行く", "to go"),
                ("今日は晴れ。", "今日", "きょう", "今日", "today"),
                ("お腹が空いた。", "お腹が空いた", "おなかがすく", "お腹が空く", "to become hungry"),
            ]:
                with self.subTest(surface=surface):
                    result = await self.plugin.analyze_text(sentence)
                    match = next(w for w in result["words"] if w["word"]["surface"] == surface)
                    word = match["word"]
                    self.assertEqual(sentence[word["start"]:word["end"]], surface)
                    entry = match["lookup"]["entries"][0]
                    self.assertEqual((entry["headword"], entry["reading"]), (head, reading))
                    self.assertIn(meaning, entry["senses"][0]["glosses"])
                    if surface == "お腹が空いた":
                        self.assertEqual(word["reading"], "おなかがすいた")
                    if surface == "行った":
                        self.assertEqual(word["reading"], "イッタ")
                        past = match["components"][-1]["lookup"]["entries"][0]
                        self.assertIn("auxiliary verb", past["senses"][0]["pos"])
                    if surface == "帰っちゃった":
                        self.assertEqual([k["character"] for k in match["lookup"]["kanji"]], ["帰"])
            bridge = (await self.plugin.lookup_word("橋"))["kanji"][0]
            self.assertIn("キョウ", bridge["on_readings"])
            self.assertIn("はし", bridge["kun_readings"])
            self.assertEqual(bridge["meanings"], ["bridge"])
            for sentence in ("気を つけて", "気を。つけて"):
                result = await self.plugin.analyze_text(sentence)
                self.assertFalse(any(w["word"]["surface"] == "気をつけて" for w in result["words"]))
        asyncio.run(check())

    def test_selected_entry_to_exported_tsv(self):
        self.use_japanese_engines()
        root = Path(main.PLUGIN_DIR)
        if not shutil.which("node") or not (root / "node_modules/typescript").exists():
            self.skipTest("Install frontend development dependencies for the card integration test")
        async def check():
            sentence = "彼は学校に行った。"
            result = await self.plugin.analyze_text(sentence)
            for surface, head, reading, gloss in [
                ("行った", "行く", "いく", "to go"),
                ("行った", "た", "た", "did"),
                ("彼", "彼", "あれ", "that"),
            ]:
                request = dict(result=result, surface=surface, headword=head, reading=reading)
                payload = subprocess.check_output(["node", str(root / "tests/vocabulary.cjs")],
                                                  input=json.dumps(request), text=True)
                self.assertTrue((await self.plugin.save_word(*json.loads(payload)))["saved"])
                with open(await self.plugin.export_vocab_csv(), encoding="utf-8") as stream:
                    row = list(csv.DictReader(stream, delimiter="\t"))[-1]
                self.assertEqual((row["word"], row["reading"], row["context"]), (head, reading, sentence))
                self.assertIn(gloss, row["meaning"])
        asyncio.run(check())

    def test_yomitan_reading_and_structured_gloss(self):
        plugin = self.plugin
        archive = Path(plugin.data_dir, "dict.zip")
        gloss = {"type": "structured-content", "content": {"tag": "ol", "content": [
            {"tag": "li", "content": "bridge"}, {"tag": "li", "content": ["a ", {"tag": "span", "content": "crossing"}]}]}}
        def write_archive(terms):
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("index.json", '{"title":"fixture","format":3}')
                zf.writestr("term_meta_bank_1.json", "[]")
                if terms:
                    zf.writestr("term_bank_1.json", json.dumps([["橋", "はし", "", "", 0, [gloss], 1, ""]]))
        async def check():
            write_archive(True)
            response = await plugin.import_yomitan_dict(str(archive))
            self.assertTrue(response["imported"])
            self.assertEqual(response["ignored_banks"], ["term_meta"])
            # Old installations have the same glossary JSON but no reading index.
            with sqlite3.connect(Path(plugin.data_dir, "custom_dicts.db")) as db:
                db.execute("DROP INDEX idx_fixture_reading")
            entry = (await plugin.lookup_word("はし"))["entries"][0]
            self.assertEqual(entry["headword"], "橋")
            self.assertEqual(entry["senses"][0]["glosses"], ["bridge\na crossing"])
            write_archive(False)
            self.assertFalse((await plugin.import_yomitan_dict(str(archive)))["imported"])
            self.assertEqual(len(await plugin.lookup_custom_dict("橋")), 1)
        asyncio.run(check())

    def test_restricted_readings_and_senses(self):
        def sense(gloss, spellings=(), readings=(), pos=()):
            return SimpleNamespace(gloss=[gloss], stagk=spellings, stagr=readings, pos=pos, misc=[])
        entry = SimpleNamespace(
            idseq=1, kanji_forms=[SimpleNamespace(text=k) for k in ("開く", "空く")],
            kana_forms=[SimpleNamespace(text="ひらく", restr=["開く"], nokanji=False),
                        SimpleNamespace(text="あく", restr=[], nokanji=False)],
            senses=[sense("open", ["開く"], pos=["verb"]), sense("become vacant", ["空く"], ["あく"])],
        )
        choices = list(jmdict_entries(entry, "空く"))
        self.assertEqual([c["reading"] for c in choices], ["あく"])
        self.assertEqual(choices[0]["senses"], [{"glosses": ["become vacant"], "pos": ["verb"]}])

    def test_staggered_vertical_columns(self):
        def box(text, x, y, vertical=True):
            return {"text": text, "is_vertical": vertical, "chars": [{"bbox": [x, y, x + 16, y + 80]}]}
        boxes = [box("左", 10, 0), box("欄外", 200, 1, False), box("右", 38, 8), box("次", 10, 200)]
        self.assertEqual([b["text"] for b in self.plugin._order_vertical_boxes(boxes)], ["右", "欄外", "左", "次"])
