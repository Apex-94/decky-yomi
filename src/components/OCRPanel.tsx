import { useState, useEffect, FC } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  DropdownItem,
  TextField,
} from "@decky/ui";
import { call } from "../utils/diagnostics";
import { shortcutManager, resolveRegionBox } from "../utils/shortcutManager";
import type { Region, Settings, AnalysisResult } from "../utils/types";

interface OCRPanelProps {
  onCapture: (region?: Region) => void;
  loading: boolean;
  text: string;
  setText: (text: string) => void;
  onAnalyze: (result: AnalysisResult) => void;
}

export const OCRPanel: FC<OCRPanelProps> = ({ onCapture, loading, onAnalyze, text, setText }) => {
  const [error, setError] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [ocrMode, setOcrMode] = useState<string>("bottom_30");
  const [settingsCache, setSettingsCache] = useState<Settings | null>(null);

  useEffect(() => {
    call<[], Settings>("get_settings")
      .then((s) => {
        setSettingsCache(s);
        if (s.ocr_mode === "region") {
          setOcrMode("bottom_30");
        } else {
          setOcrMode(s.ocr_mode || "bottom_30");
        }
      })
      .catch((e) => setError(String(e)));
  }, []);

  const handleModeChange = async (newMode: string) => {
    setOcrMode(newMode);
    try {
      const updated = await call<[Record<string, any>], Settings>("update_settings", { ocr_mode: newMode });
      setSettingsCache(updated);
      shortcutManager.showRegionPreview(resolveRegionBox(updated), false);
    } catch (e) {
      setError(String(e));
    }
  };

  const handleCapture = () => {
    setError("");
    onCapture(); // calls backend capture_and_analyze with null -> automatically uses configured preset/custom box!
  };

  return (
    <>
      <PanelSection title="📸 OCR Capture">
        <PanelSectionRow>
          <DropdownItem
            label="Capture Region Preset"
            description="Isolate dialogue box for highest OCR accuracy"
            rgOptions={[
              { label: "VN Dialogue Box (Bottom 30%)", data: "bottom_30" },
              { label: "VN Dialogue Box (Bottom 40%)", data: "bottom_40" },
              { label: "Screen Center (50%)", data: "center" },
              { label: "Custom Region (Settings)", data: "custom" },
              { label: "Full Screen (100%)", data: "fullscreen" },
            ]}
            selectedOption={ocrMode}
            onChange={(opt) => handleModeChange(opt.data)}
          />
        </PanelSectionRow>

        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={handleCapture}
            disabled={loading}
          >
            {loading ? "⏳ Processing OCR..." : "🎯 Capture & Analyze"}
          </ButtonItem>
        </PanelSectionRow>

        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => {
              const box = resolveRegionBox({ ...(settingsCache || {}), ocr_mode: ocrMode as Settings["ocr_mode"] });
              shortcutManager.showRegionPreview(box, false);
            }}
          >
            👁️ Preview OCR Box Guide (4.5s)
          </ButtonItem>
        </PanelSectionRow>

        <PanelSectionRow>
          <TextField label="Enter or correct Japanese text" value={text} onChange={(e) => setText(e.target.value)} />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={!text.trim() || analyzing || loading}
            onClick={async () => {
              setAnalyzing(true);
              setError("");
              try {
                onAnalyze(await call<[string], AnalysisResult>("analyze_text", text));
              } catch (e) {
                setError(String(e));
              } finally {
                setAnalyzing(false);
              }
            }}
          >
            {analyzing ? "Looking up…" : "Look up text"}
          </ButtonItem>
        </PanelSectionRow>
        {error && <PanelSectionRow><div role="alert">{error}</div></PanelSectionRow>}
      </PanelSection>

      {/* Instructions */}
      <PanelSection title="ℹ️ How to Use">
        <PanelSectionRow>
          <div
            style={{
              fontSize: "11px",
              opacity: 0.7,
              padding: "4px 8px",
              lineHeight: "1.5",
            }}
          >
            1. Navigate to Japanese dialogue in your game (or visual novel).
            <br />
            2. Set region preset to <b>"VN Dialogue Box (Bottom 30%)"</b> (or customize in Settings).
            <br />
            3. Press <b>"Capture & Analyze"</b> or hold rear grip button (<b>L4</b>).
            <br />
            4. View instant furigana & definitions in the Results tab or in-game HUD!
          </div>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};
