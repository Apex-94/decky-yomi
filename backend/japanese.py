"""Sentence-local Japanese dictionary lookup and expression matching."""
from contextlib import closing, contextmanager
import json
import logging
import re
import sqlite3
import unicodedata


def hiragana(text):
    return "".join(chr(ord(c) - 96) if "ァ" <= c <= "ヶ" else c for c in text)


def glossary_text(node):
    """Extract readable text from a Yomitan definition, including old imported rows."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(glossary_text(child) for child in node)
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    if node.get("type") == "image" or node.get("tag") == "img":
        return node.get("description") or node.get("alt") or node.get("title") or "[image]"
    tag = node.get("tag")
    content = glossary_text(node.get("content"))
    if tag == "rt":
        return f"({content})"
    if tag in {"br", "li", "div", "p", "tr", "summary"}:
        return content + "\n"
    if tag in {"td", "th"}:
        return content + " "
    return content


@contextmanager
def custom_dictionaries(path):
    """Reuse one connection and the existing expression/reading indexes per sentence."""
    if not path.exists():
        yield lambda term, limit=None: []
        return

    with closing(sqlite3.connect(path)) as conn:
        try:
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'dict_*'"
            ) if row[0].replace("_", "").isalnum()]
        except sqlite3.DatabaseError as error:
            logging.getLogger(__name__).warning("Could not read imported dictionaries: %s", error)
            tables = []
        for table in tables:
            # Older releases only indexed expressions. Also index their readings.
            try:
                conn.execute(f"CREATE INDEX IF NOT EXISTS [idx_{table[5:]}_reading] ON [{table}](reading)")
            except sqlite3.OperationalError as error:
                logging.getLogger(__name__).warning("Reading index unavailable for %s: %s", table, error)

        def lookup(term, limit=None):
            matches = []
            for table in tables:
                rows = conn.execute(
                    f"SELECT expression, reading, glossary, score FROM [{table}] "
                    "WHERE expression = ? OR reading = ? ORDER BY (expression = ?) DESC, score DESC LIMIT ?",
                    (term, term, term, -1 if limit is None else limit),
                )
                for expression, reading, glossary, score in rows:
                    definitions = json.loads(glossary)
                    if not isinstance(definitions, list):
                        definitions = [definitions]
                    matches.append({
                        "dict": table[5:], "expression": expression, "reading": reading,
                        "glossary": [glossary_text(g).strip() for g in definitions],
                        "score": score,
                    })
            return matches

        yield lookup


POS_HINTS = {
    "助動詞": ("auxiliary verb", "auxiliary adjective", "copula"),
    "助詞": ("particle",), "動詞": ("verb",), "形容詞": ("adjective",),
    "名詞": ("noun",), "代名詞": ("pronoun",), "副詞": ("adverb",),
    "感動詞": ("interjection",), "接続詞": ("conjunction",),
}


def jmdict_entries(entry, term):
    """Yield legal headword/reading pairs, each with its applicable senses."""
    spellings = [k.text for k in entry.kanji_forms]
    for form in entry.kana_forms:
        compatible = [] if form.nokanji else (form.restr or spellings)
        if term in spellings:
            if term not in compatible:
                continue
            head = term
        elif term == form.text:
            usually_kana = any("word usually written using kana alone" in sense.misc for sense in entry.senses)
            head = term if usually_kana or not compatible else compatible[0]
        else:
            continue

        senses = []
        pos = []
        for sense in entry.senses:
            # An omitted POS inherits the preceding sense's tags.
            pos = sense.pos or pos
            if sense.stagr and form.text not in sense.stagr:
                continue
            if sense.stagk:
                allowed = [head] if head in spellings else compatible
                if not any(k in sense.stagk for k in allowed):
                    continue
            senses.append({"glosses": [str(g) for g in sense.gloss], "pos": list(pos)})
        if senses:
            yield {"kanji": compatible, "kana": [form.text], "headword": head,
                   "reading": form.text, "senses": senses, "id": entry.idseq}


def word_terms(word):
    return list(dict.fromkeys(filter(None, (word.get("base"), word["surface"], word.get("lemma")))))


def span_word(parts):
    last = parts[-1]
    prefix = "".join(p["surface"] for p in parts[:-1])
    prefix_reading = "".join(p["reading"] for p in parts[:-1])
    return {**last, "start": parts[0]["start"], "surface": prefix + last["surface"],
            "base": prefix + last.get("base", last["surface"]), "lemma": prefix + last["lemma"],
            "reading": prefix_reading + last["reading"] if all(p["reading"] for p in parts) else "",
            "base_reading": prefix_reading + last.get("base_reading", "") if all(p["reading"] for p in parts) else ""}


def inflected_reading(base, surface, reading):
    """Transfer an unambiguous kana ending change to a dictionary reading."""
    common = 0
    for a, b in zip(base, surface):
        if a != b:
            break
        common += 1
    removed, added = hiragana(base[common:]), hiragana(surface[common:])
    reading = hiragana(reading)
    if all("ぁ" <= c <= "ゖ" for c in removed + added) and reading.endswith(removed):
        return reading[:len(reading) - len(removed)] + added
    return None


def inflection_end(words, end):
    """Extend a predicate through contiguous auxiliaries and connective て/で."""
    if not words[end - 1]["pos"].startswith(("動詞", "形容詞", "助動詞")):
        return end
    while end < len(words) and words[end - 1]["end"] == words[end]["start"]:
        current, previous = words[end], words[end - 1]
        connective = current["pos"].startswith("助詞,接続助詞") and current["surface"] in ("て", "で")
        helper = (previous["surface"] in ("て", "で") and current["pos"].startswith("動詞,非自立")
                  and current.get("base") in ("いる", "おく", "しまう", "みる", "くる", "いく"))
        if not (current["pos"].startswith("助動詞") or connective or helper):
            break
        end += 1
    return end


class JapaneseLookup:
    def __init__(self, dictionary, custom_lookup, settings):
        self.dictionary = dictionary
        self.custom_lookup = custom_lookup
        self.limit = settings.get("max_results", 5)
        self.show_kanji = settings.get("show_kanji_detail", True)
        self.cache = {}
        self.characters = {}
        self.dictionary_error = None

    def candidates(self, term):
        if term not in self.cache:
            entries = []
            is_pattern = term.startswith("id#") or any(c in term for c in "%_@")
            if self.dictionary and not self.dictionary_error and not is_pattern:
                try:
                    matches = self.dictionary.lookup(term, lookup_chars=False, lookup_ne=False).entries
                    entries = [e for e in matches if any(f.text == term for f in (*e.kanji_forms, *e.kana_forms))]
                except (LookupError, RuntimeError) as error:
                    self.dictionary_error = str(error)
                    logging.getLogger(__name__).warning("JMdict unavailable: %s", error)
            self.cache[term] = entries, self.custom_lookup(term)
        return self.cache[term]

    def kanji(self, surface):
        result = []
        if not self.show_kanji or not self.dictionary or self.dictionary_error:
            return result
        for char in dict.fromkeys(surface):
            if not unicodedata.name(char, "").startswith(("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH")):
                continue
            if char not in self.characters:
                try:
                    ch = self.dictionary.get_char(char)
                except LookupError:
                    ch = None
                self.characters[char] = None if not ch else {
                    "character": char, "meanings": ch.meanings(english_only=True),
                    "on_readings": [r.value for g in ch.rm_groups for r in g.on_readings],
                    "kun_readings": [r.value for g in ch.rm_groups for r in g.kun_readings],
                }
            if self.characters[char]:
                result.append(self.characters[char])
        return result

    def lookup(self, word):
        reading = hiragana(word.get("base_reading") or word["reading"])
        hints = POS_HINTS.get(word["pos"].split(",")[0], ())
        terms = word_terms(word)
        if not terms:
            return {"query": "", "entries": [], "kanji": []}
        ranked = {}
        for index, term in enumerate(terms):
            raw, custom = self.candidates(term)
            for item in raw:
                for entry in jmdict_entries(item, term):
                    tags = {p for sense in entry["senses"] for p in sense["pos"]}
                    pos_match = any(re.search(r"\b" + hint + r"\b", tag) for hint in hints for tag in tags)
                    common_form = any(f.pri for f in item.kana_forms if f.text == entry["reading"])
                    score = (index == 0, hiragana(entry["reading"]) == reading, pos_match, -index, common_form)
                    key = ("jmdict", entry["id"], entry["reading"])
                    if key not in ranked or ranked[key][0] < score:
                        ranked[key] = (score, entry, term, "jmdict")
            for cm in custom:
                kana = cm["reading"] or cm["expression"]
                source = "dict:" + cm["dict"]
                key = (source, cm["expression"], kana, tuple(cm["glossary"]))
                if key not in ranked:
                    entry = {
                        "headword": cm["expression"], "reading": kana, "id": None,
                        "kanji": [cm["expression"]], "kana": [kana],
                        "senses": [{"glosses": cm["glossary"], "pos": [source]}],
                    }

                    score = (index == 0, hiragana(kana) == reading, False, -index, cm["score"] > 0)
                    ranked[key] = (score, entry, term, source)

        matches = sorted(ranked.values(), key=lambda match: match[0], reverse=True)
        entries = []
        # Apply each dictionary's limit only after comparing the matching forms.
        counts = {}
        for _, entry, _, source in matches:
            if counts.get(source, 0) < self.limit:
                entries.append(entry)
                counts[source] = counts.get(source, 0) + 1
        result = {"query": matches[0][2] if matches else terms[0], "entries": entries,
                  "kanji": self.kanji(word["surface"])}
        if not entries and (self.dictionary is None or self.dictionary_error):
            result["error"] = self.dictionary_error or "Dictionary not loaded"
        return result

    def phrase_matches(self, candidate):
        for term in word_terms(candidate):
            raw, custom = self.candidates(term)
            if not (raw or custom):
                continue
            # A trailing particle often belongs to sentence grammar. For
            # example 今日 + は (きょうは) can also spell the greeting こんにちは.
            if candidate["pos"].startswith("助詞") and candidate["base_reading"]:
                expected = hiragana(candidate["base_reading"])
                readings = (r.text for item in raw for r in item.kana_forms
                            if not r.restr or term in r.restr or term == r.text)
                if not (any(hiragana(r) == expected for r in readings)
                        or any(hiragana(c["reading"] or c["expression"]) == expected for c in custom)):
                    continue
            return True
        return False

    def analyze(self, words, skip_particles):
        results = []
        start = 0
        while start < len(words):
            first = words[start]
            lexical_end = start + 1
            end = inflection_end(words, lexical_end)
            best = first
            # Examine contiguous token spans only, stopping at punctuation or whitespace.
            for stop in range(start + 2, min(len(words), start + 8) + 1):
                left, right = words[stop - 2:stop]
                if (left["end"] != right["start"] or
                        any(p["pos"].startswith(("記号", "補助記号", "空白")) for p in (left, right))):
                    break
                extended = inflection_end(words, stop)
                if extended < end or (extended == end and end > lexical_end):
                    continue
                candidate = span_word(words[start:stop])
                if self.phrase_matches(candidate):
                    best, lexical_end, end = candidate, stop, extended
            parts = words[start:end]
            skipped = len(parts) == 1 and skip_particles and first["pos"].startswith(("助詞", "記号", "補助記号", "空白"))
            if end > lexical_end:
                best = {**best, "end": parts[-1]["end"], "surface": "".join(w["surface"] for w in parts),
                        "reading": "".join(w["reading"] for w in parts) if all(w["reading"] for w in parts) else ""}
            lookup = None if skipped else self.lookup(best)
            if lexical_end > start + 1 and lookup["entries"]:
                entry = lookup["entries"][0]
                # A known phrase can disambiguate a component reading: お腹が空く
                # reads おなかがすく even if UniDic chose あく for 空く alone.
                if entry["headword"] == best["base"] and hiragana(best["base_reading"]) != hiragana(entry["reading"]):
                    reading = inflected_reading(best["base"], best["surface"], entry["reading"])
                    if reading:
                        best = {**best, "reading": reading}
            item = {"word": best, "skipped": skipped, "lookup": lookup}
            if len(parts) > 1:
                item["components"] = [{"word": w, "skipped": False, "lookup": self.lookup(w)} for w in parts]
            results.append(item)
            start = end
        return results
