// ─────────────────────────────────────────────────────────────
// Shared TypeScript types for Decky Yomi
// ─────────────────────────────────────────────────────────────

/** A single morpheme/word from MeCab segmentation */
export interface WordInfo {
  surface: string;
  reading: string;
  lemma: string;
  pos: string;
}

/** A single dictionary sense (meaning + POS) */
export interface DictSense {
  glosses: string[];
  pos: string[];
}

/** A dictionary entry for a word */
export interface DictEntry {
  headword?: string;
  reading?: string;
  kanji: string[];
  kana: string[];
  senses: DictSense[];
  id: number | null;
}

/** Kanji character details from KANJIDIC2 */
export interface KanjiInfo {
  character: string;
  meanings: string[];
  on_readings: string[];
  kun_readings: string[];
}

/** Result of looking up a single word */
export interface LookupResult {
  query: string;
  entries: DictEntry[];
  kanji: KanjiInfo[];
  error?: string;
}

/** A word in the analysis results (word + its lookup) */
export interface WordResult {
  components?: WordResult[];
  word: WordInfo;
  lookup: LookupResult | null;
  skipped: boolean;
}

/** Full pipeline result from capture_and_analyze */
export interface AnalysisResult {
  raw_text: string;
  words: WordResult[];
  image_path: string;
  timestamp: string;
  error?: string;
}

/** Plugin settings */
export interface Settings {
  ocr_mode: "fullscreen" | "bottom_30" | "bottom_40" | "center" | "custom" | "region";
  custom_region_x?: number;
  custom_region_y?: number;
  custom_region_w?: number;
  custom_region_h?: number;
  ocr_engine: "manga_ocr" | "meikiocr";
  ocr_orientation: "auto" | "horizontal" | "vertical";
  ocr_preprocess: boolean;
  hotkey_trigger: "L4" | "R4" | "L5" | "R5" | "select" | "none";
  trackpad_haptics: boolean;
  auto_segment: boolean;
  show_readings: boolean;
  show_pos: boolean;
  show_kanji_detail: boolean;
  show_frequency: boolean;
  show_pitch_accent: boolean;
  auto_copy_clipboard: boolean;
  toast_quick_result: boolean;
  toast_duration: number;
  toast_detail: "compact" | "full";
  max_results: number;
  skip_particles: boolean;
  anki_deck_name: string;
  tag_game_name: boolean;
  theme: "dark" | "light";
  font_size: "small" | "medium" | "large";
  log_ocr_text?: boolean;
  spreadsheet_safe_export?: boolean;
}

/** Saved vocabulary entry */
export interface VocabEntry {
  id: number;
  word: string;
  reading: string;
  meaning: string;
  pos: string;
  context_sentence: string;
  game_name: string;
  created_at: string;
  review_count: number;
  last_reviewed: string | null;
}

/** Engine status from backend */
export interface EngineStatus {
  ocr: boolean;
  segmentation: boolean;
  dictionary: boolean;
}

/** Region coordinates for selection */
export interface Region {
  x: number;
  y: number;
  w: number;
  h: number;
  unit?: "percent" | "pixel";
  error?: string;
}

/** Custom Yomitan dictionary lookup result */
export interface CustomDictResult {
  dict: string;
  expression: string;
  reading: string;
  glossary: string[];
  score: number;
}

/** Component information for in-UI downloader */
export interface ComponentInfo {
  id: string;
  name: string;
  desc: string;
  installed: boolean;
  size: string;
  packages: string[];
}

/** Overall download/component status */
export interface ComponentsStatus {
  components: ComponentInfo[];
  all_installed: boolean;
  install_dir: string;
  installing: boolean;
  install_progress: InstallProgress;
}

/** Live progress of an installation */
export interface InstallProgress {
  status: "idle" | "installing" | "completed" | "error";
  component: string | null;
  current_step: string;
  percent: number;
  logs: string[];
  error: string | null;
}
