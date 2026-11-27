import { surfaceReading } from "../utils/readings";
import { useState, useEffect, FC } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  Focusable,
  DialogButton,
} from "@decky/ui";
import { call, trace } from "../utils/diagnostics";
import { toaster } from "@decky/api";
import { shortcutManager } from "../utils/shortcutManager";
import { OCRPanel } from "./OCRPanel";
import { DictionaryResults } from "./DictionaryResults";
import { VocabList } from "./VocabList";
import { SettingsPanel } from "./SettingsPanel";
import { DownloadManager } from "./DownloadManager";
import type { AnalysisResult, EngineStatus, Settings } from "../utils/types";

type Tab = "ocr" | "results" | "vocab" | "settings" | "setup";

const TAB_LABELS: Record<Tab, string> = {
  ocr: "📸 OCR",
  results: "📖 Results",
  vocab: "📚 Vocab",
  settings: "⚙️ Settings",
  setup: "📥 Setup",
};

export const MainPanel: FC = () => {
  const [activeTab, setActiveTab] = useState<Tab>("ocr");
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(null);
  const [text, setText] = useState("");
  useEffect(() => {
    let previous: AnalysisResult | null = null;
    return shortcutManager.subscribe(({ result }) => {
      if (result?.raw_text && result !== previous) {
        previous = result;
        setText(result.raw_text);
        setAnalysisResult(result);
      }
    });
  }, []);
  const [loading, setLoading] = useState(false);
  const [engineStatus, setEngineStatus] = useState<EngineStatus | null>(null);

  useEffect(() => { trace("ui.tab", activeTab); }, [activeTab]);
  useEffect(() => { trace("ui.mount"); return () => trace("ui.unmount"); }, []);

  // Check engine status on mount
  useEffect(() => {
    call<[], EngineStatus>("get_engine_status").then(setEngineStatus).catch(() => {});
  }, []);

  const handleCapture = async (region?: any) => {
    setLoading(true);
    try {
      const result = await call<[region: any], AnalysisResult>(
        "capture_and_analyze",
        region ?? null
      );
      setAnalysisResult(result);
      if (result.raw_text) setText(result.raw_text);
      setActiveTab("results");
      if (!result.error) {

        // Quick toast notification if enabled
        try {
          const settings = await call<[], Settings>("get_settings");
          if (settings.toast_quick_result && result.words) {
            const firstValid = result.words.find(
              (w) => !w.skipped && w.lookup?.entries && w.lookup.entries.length > 0
            );
            if (firstValid && firstValid.lookup?.entries?.[0]) {
              const entry = firstValid.lookup.entries[0];
              const reading = surfaceReading(firstValid.word);
              const gloss = entry.senses?.[0]?.glosses?.slice(0, 2).join(", ");
              const isCompact = settings.toast_detail === "compact";
              const durationMs = (settings.toast_duration || 4) * 1000;

              toaster.toast({
                title: `Decky Yomi: ${firstValid.word.surface} ${reading ? `(${reading})` : ""}`,
                body: isCompact ? (reading || "Word detected") : (gloss || "Looked up successfully"),
                duration: durationMs,
              });
            } else if (result.raw_text) {
              toaster.toast({
                title: "Decky Yomi — Text Detected",
                body: result.raw_text.slice(0, 50),
                duration: (settings.toast_duration || 4) * 1000,
              });
            }
          }
        } catch {
          // Ignore toast errors if not available
        }
      } else {
        toaster.toast({
          title: "Decky Yomi — Error",
          body: result.error,
          duration: 4000,
        });
      }
    } catch (e) {
      setActiveTab("results");
      console.error("Decky Yomi: capture failed", e);
      setAnalysisResult({
        error: `Frontend error: ${e}`,
        raw_text: "",
        words: [],
        image_path: "",
        timestamp: new Date().toISOString(),
      });
    }
    setLoading(false);
  };

  return (
    <div style={{ boxSizing: "border-box", width: "100%", maxWidth: "100%", minWidth: 0, padding: "0 12px 80px" }}>
      {/* Engine Status Banner */}
      {engineStatus && (!engineStatus.ocr || !engineStatus.dictionary) && activeTab !== "setup" && (
        <PanelSection>
          <PanelSectionRow>
            <div
              style={{
                padding: "8px 12px",
                background: "rgba(255, 165, 0, 0.15)",
                borderRadius: "8px",
                fontSize: "12px",
                border: "1px solid rgba(255, 165, 0, 0.3)",
              }}
            >
              ⚠️ Engines not downloaded yet.
              <div style={{ marginTop: "6px" }}>
                <ButtonItem layout="below" onClick={() => setActiveTab("setup")}>
                  📥 Open In-UI Downloader
                </ButtonItem>
              </div>
            </div>
          </PanelSectionRow>
        </PanelSection>
      )}

      {/* Tab Navigation */}
      <PanelSection>
        <Focusable
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
            gap: "4px",
            padding: "0",
          }}
        >
          {(Object.keys(TAB_LABELS) as Tab[]).map((tab) => (
            <DialogButton
              style={{ boxSizing: "border-box", width: "100%", minWidth: 0, margin: 0, padding: "10px 6px", textAlign: "center", fontSize: 12, boxShadow: activeTab === tab ? "inset 0 -3px #66c0f4" : "none" }}
              key={tab}
              onClick={() => setActiveTab(tab)}

            >
              {TAB_LABELS[tab]}
            </DialogButton>
          ))}
        </Focusable>
      </PanelSection>

      {/* Tab Content */}
      {activeTab === "ocr" && (
        <OCRPanel text={text} setText={setText} onCapture={handleCapture} loading={loading} onAnalyze={(result) => { setAnalysisResult(result); setActiveTab("results"); }} />
      )}
      {activeTab === "results" && (
        <DictionaryResults
          result={analysisResult}
          onSaveWord={() => {
            // Optionally switch to vocab tab after save
          }}
        />
      )}
      {activeTab === "vocab" && <VocabList />}
      {activeTab === "settings" && <SettingsPanel />}
      {activeTab === "setup" && (
        <DownloadManager
          onInstalled={() => {
            call<[], EngineStatus>("get_engine_status").then(setEngineStatus).catch(() => {});
          }}
        />
      )}
    </div>
  );
};
