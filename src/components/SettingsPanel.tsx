import { useEffect, useState, FC } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ToggleField,
  SliderField,
  DropdownItem,
  ButtonItem,
} from "@decky/ui";
import { call } from "../utils/diagnostics";
import { openFilePicker, FileSelectionType, toaster } from "@decky/api";
import { DownloadManager } from "./DownloadManager";
import { shortcutManager, resolveRegionBox } from "../utils/shortcutManager";
import type { Settings } from "../utils/types";

export const SettingsPanel: FC = () => {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [saving, setSaving] = useState(false);
  const [importingDict, setImportingDict] = useState(false);
  const [importStatus, setImportStatus] = useState<string | null>(null);
  const [pinPreview, setPinPreview] = useState(false);

  useEffect(() => {
    call<[], Settings>("get_settings")
      .then(setSettings)
      .catch((e) => setImportStatus("Could not load settings. Reopen this tab to retry: " + String(e)));
  }, []);

  const updateSetting = async (key: keyof Settings, value: any) => {
    if (!settings) return;
    const optimistic = { ...settings, [key]: value };
    if (
      key === "ocr_mode" ||
      key === "custom_region_x" ||
      key === "custom_region_y" ||
      key === "custom_region_w" ||
      key === "custom_region_h"
    ) {
      shortcutManager.showRegionPreview(resolveRegionBox(optimistic), pinPreview);
    }
    setSaving(true);
    try {
      const updated = await call<[dict: Record<string, any>], Settings>(
        "update_settings",
        { [key]: value }
      );
      setSettings(updated);
    } catch (e) {
      setImportStatus("Could not save settings: " + String(e));
    }
    setSaving(false);
  };

  const handleImportYomitanDict = async () => {
    setImportingDict(true);
    setImportStatus(null);
    try {
      const res = await openFilePicker(
        FileSelectionType.FILE,
        "/home/deck",
        true,
        false,
        undefined,
        ["zip"]
      );

      const targetPath = res.realpath || res.path;
      if (targetPath) {
        setImportStatus(`Importing ${targetPath.split("/").pop()}...`);
        const result = await call<[string], { imported: boolean; title?: string; terms?: number; error?: string }>(
          "import_yomitan_dict",
          targetPath
        );

        if (result.imported) {
          const msg = `Imported "${result.title}" (${result.terms} terms)`;
          setImportStatus(`✅ ${msg}`);
          try {
            toaster.toast({
              title: "Decky Yomi",
              body: msg,
              duration: 4000,
            });
          } catch {}
        } else {
          setImportStatus(`❌ Import failed: ${result.error}`);
        }
      }
    } catch (e) {
      console.error("Yomitan dict file picker failed:", e);
      setImportStatus("Import cancelled or failed");
    }
    setImportingDict(false);
  };

  if (!settings) {
    return (
      <PanelSection title="⚙️ Settings">
        <PanelSectionRow>
          <div style={{ textAlign: "center", opacity: 0.5 }}>
            {importStatus || "Loading settings…"}
          </div>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  return (
    <>
      {/* ── Hardware Controls & Shortcuts ── */}
      <PanelSection title="🎮 In-Game Shortcut & Overlay">
        <PanelSectionRow>
          <DropdownItem
            label="In-Game Shortcut Button"
            description="Hold for 0.5s to capture dialogue & show overlay without opening QAM"
            rgOptions={[
              { label: "L4 (Rear Left Upper)", data: "L4" },
              { label: "R4 (Rear Right Upper)", data: "R4" },
              { label: "L5 (Rear Left Lower)", data: "L5" },
              { label: "R5 (Rear Right Lower)", data: "R5" },
              { label: "Select / View Button", data: "select" },
              { label: "Disabled", data: "none" },
            ]}
            selectedOption={settings.hotkey_trigger || "L4"}
            onChange={(opt) => {
              updateSetting("hotkey_trigger", opt.data);
              shortcutManager.refreshSettings();
            }}
          />
        </PanelSectionRow>
      </PanelSection>

      {/* ── OCR Engine & Processing ── */}
      <PanelSection title="🔧 OCR & Pre-Processing">
        <PanelSectionRow>
          <DropdownItem
            label="OCR Engine"
            description="MeikiOCR is much faster and trained for game/VN text; Manga-OCR is best for manga"
            rgOptions={[
              { label: "MeikiOCR (Fast Game/VN ONNX)", data: "meikiocr" },
              { label: "Manga-OCR (Vertical Manga / PyTorch)", data: "manga_ocr" },
            ]}
            selectedOption={settings.ocr_engine || "meikiocr"}
            onChange={(opt) => updateSetting("ocr_engine", opt.data)}
          />
        </PanelSectionRow>


        <PanelSectionRow>
          <DropdownItem
            label="Capture Region Preset"
            description="Area of screen fed into Japanese OCR"
            rgOptions={[
              { label: "VN Dialogue Box (Bottom 30%)", data: "bottom_30" },
              { label: "VN Dialogue Box (Bottom 40%)", data: "bottom_40" },
              { label: "Screen Center (50%)", data: "center" },
              { label: "Custom Region (Sliders)", data: "custom" },
              { label: "Full Screen (100%)", data: "fullscreen" },
            ]}
            selectedOption={
              settings.ocr_mode === "region" ? "bottom_30" : (settings.ocr_mode || "bottom_30")
            }
            onChange={(opt) => updateSetting("ocr_mode", opt.data)}
          />
        </PanelSectionRow>

        {(() => {
          const box = resolveRegionBox(settings);
          return (
            <>
              <PanelSectionRow>
                <div style={{ width: "100%", padding: "4px 0" }}>
                  <div
                    style={{
                      position: "relative",
                      width: "100%",
                      aspectRatio: "16 / 10",
                      background: "linear-gradient(135deg, #121722 0%, #1b2434 100%)",
                      borderRadius: "6px",
                      border: "1px solid rgba(255,255,255,0.18)",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        position: "absolute",
                        left: `${box.x}%`,
                        top: `${box.y}%`,
                        width: `${box.w}%`,
                        height: `${box.h}%`,
                        border: "1.5px dashed #58a6ff",
                        background: "rgba(88, 166, 255, 0.16)",
                        boxSizing: "border-box",
                      }}
                    >
                      <div style={{ position: "absolute", left: 0, right: 0, top: "50%", borderTop: "1px solid rgba(255, 215, 0, 0.55)" }} />
                      <div style={{ position: "absolute", top: 0, bottom: 0, left: "50%", borderLeft: "1px solid rgba(255, 215, 0, 0.45)" }} />
                    </div>
                    <div
                      style={{
                        position: "absolute",
                        bottom: "4px",
                        right: "6px",
                        fontSize: "10px",
                        color: "rgba(255,255,255,0.75)",
                        background: "rgba(0,0,0,0.55)",
                        padding: "1px 5px",
                        borderRadius: "4px",
                      }}
                    >
                      {box.x}%, {box.y}% · {box.w}%×{box.h}%
                    </div>
                  </div>
                </div>
              </PanelSectionRow>
              <PanelSectionRow>
                <ToggleField
                  label="Show Helper Lines on Screen"
                  description="Overlay guide lines over the game while positioning"
                  checked={pinPreview}
                  onChange={(v) => {
                    setPinPreview(v);
                    if (v) {
                      shortcutManager.showRegionPreview(box, true);
                    } else {
                      shortcutManager.hideRegionPreview();
                    }
                  }}
                />
              </PanelSectionRow>
            </>
          );
        })()}

        {settings.ocr_mode === "custom" && (
          <>
            <PanelSectionRow>
              <div style={{ fontSize: "12px", opacity: 0.85, padding: "2px 0" }}>
                📐 Custom Box: ({settings.custom_region_x ?? 0}%, {settings.custom_region_y ?? 70}%) — Size: {settings.custom_region_w ?? 100}% × {settings.custom_region_h ?? 30}%
              </div>
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label="Left Offset (X)"
                description={`${settings.custom_region_x ?? 0}% from left`}
                min={0}
                max={90}
                step={5}
                value={settings.custom_region_x ?? 0}
                onChange={(v) => updateSetting("custom_region_x", v)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label="Top Offset (Y)"
                description={`${settings.custom_region_y ?? 70}% from top`}
                min={0}
                max={90}
                step={5}
                value={settings.custom_region_y ?? 70}
                onChange={(v) => updateSetting("custom_region_y", v)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label="Box Width"
                description={`${settings.custom_region_w ?? 100}% width`}
                min={10}
                max={100}
                step={5}
                value={settings.custom_region_w ?? 100}
                onChange={(v) => updateSetting("custom_region_w", v)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label="Box Height"
                description={`${settings.custom_region_h ?? 30}% height`}
                min={10}
                max={100}
                step={5}
                value={settings.custom_region_h ?? 30}
                onChange={(v) => updateSetting("custom_region_h", v)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                onClick={async () => {
                  await updateSetting("custom_region_x", 0);
                  await updateSetting("custom_region_y", 70);
                  await updateSetting("custom_region_w", 100);
                  await updateSetting("custom_region_h", 30);
                }}
              >
                Reset Custom Box to Bottom 30%
              </ButtonItem>
            </PanelSectionRow>
          </>
        )}



        <PanelSectionRow>
          <ToggleField
            label="Enhance Contrast"
            description="Boosts text contrast in transparent dialogue boxes"
            checked={settings.ocr_preprocess ?? true}
            onChange={(v) => updateSetting("ocr_preprocess", v)}
          />
        </PanelSectionRow>

        <PanelSectionRow>
          <ToggleField
            label="Auto-Copy to Clipboard"
            description="Copies extracted sentence to Linux clipboard (wl-copy)"
            checked={settings.auto_copy_clipboard ?? false}
            onChange={(v) => updateSetting("auto_copy_clipboard", v)}
          />
        </PanelSectionRow>
      </PanelSection>

      {/* ── Dictionary Display ── */}
      <PanelSection title="📖 Dictionary Display">
        <PanelSectionRow>
          <ToggleField
            label="Auto Word Segmentation"
            description="Break text into individual words automatically"
            checked={settings.auto_segment}
            onChange={(v) => updateSetting("auto_segment", v)}
          />
        </PanelSectionRow>

        <PanelSectionRow>
          <ToggleField
            label="Show Readings (Furigana)"
            description="Display kana readings above words"
            checked={settings.show_readings}
            onChange={(v) => updateSetting("show_readings", v)}
          />
        </PanelSectionRow>

        <PanelSectionRow>
          <ToggleField
            label="Show Parts of Speech"
            description="Display grammatical categories"
            checked={settings.show_pos}
            onChange={(v) => updateSetting("show_pos", v)}
          />
        </PanelSectionRow>

        <PanelSectionRow>
          <ToggleField
            label="Show Kanji Details"
            description="Meanings & readings for individual kanji"
            checked={settings.show_kanji_detail}
            onChange={(v) => updateSetting("show_kanji_detail", v)}
          />
        </PanelSectionRow>

        <PanelSectionRow>
          <ToggleField
            label="Skip Particles"
            description="Don't look up は、が、を、に, etc."
            checked={settings.skip_particles}
            onChange={(v) => updateSetting("skip_particles", v)}
          />
        </PanelSectionRow>

        <PanelSectionRow>
          <ToggleField
            label="Quick Toast Notification"
            description="Flash first result briefly on capture"
            checked={settings.toast_quick_result}
            onChange={(v) => updateSetting("toast_quick_result", v)}
          />
        </PanelSectionRow>

        {settings.toast_quick_result && (
          <>
            <PanelSectionRow>
              <SliderField
                label="Toast Duration"
                description={`${settings.toast_duration || 4} seconds`}
                min={2}
                max={8}
                step={1}
                value={settings.toast_duration || 4}
                onChange={(v) => updateSetting("toast_duration", v)}
                notchCount={7}
                notchLabels={[
                  { notchIndex: 0, label: "2s" },
                  { notchIndex: 2, label: "4s" },
                  { notchIndex: 6, label: "8s" },
                ]}
              />
            </PanelSectionRow>

            <PanelSectionRow>
              <DropdownItem
                label="Toast Content"
                rgOptions={[
                  { label: "Full (Word, Reading & Meaning)", data: "full" },
                  { label: "Compact (Word & Reading)", data: "compact" },
                ]}
                selectedOption={settings.toast_detail || "full"}
                onChange={(opt) => updateSetting("toast_detail", opt.data)}
              />
            </PanelSectionRow>
          </>
        )}
      </PanelSection>

      {/* ── Vocabulary & Mining ── */}
      

      {/* ── Advanced ── */}
      <PanelSection title="🔬 Advanced">




        <PanelSectionRow>
          <SliderField
            label="Max Results per Word"
            description={`Currently: ${settings.max_results}`}
            min={1}
            max={10}
            step={1}
            value={settings.max_results}
            onChange={(v) => updateSetting("max_results", v)}
            notchCount={10}
            notchLabels={[
              { notchIndex: 0, label: "1" },
              { notchIndex: 4, label: "5" },
              { notchIndex: 9, label: "10" },
            ]}
          />
        </PanelSectionRow>

        <PanelSectionRow>
          <ToggleField
            label="Log OCR Text Preview"
            description="Include recognized text snippets in plugin logs (off by default for privacy)"
            checked={!!settings.log_ocr_text}
            onChange={(v) => updateSetting("log_ocr_text", v)}
          />
        </PanelSectionRow>
      </PanelSection>

      {/* ── Appearance ── */}
      

      {/* ── Custom Dictionaries (Yomitan) ── */}
      <PanelSection title="📦 Yomitan Dictionaries">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={handleImportYomitanDict}
            disabled={importingDict}
          >
            {importingDict ? "⏳ Importing..." : "📁 Import Yomitan Dictionary (.zip)"}
          </ButtonItem>
        </PanelSectionRow>
        {importStatus && (
          <PanelSectionRow>
            <div
              style={{
                fontSize: "11px",
                padding: "6px 8px",
                background: "rgba(255, 255, 255, 0.05)",
                borderRadius: "4px",
                wordBreak: "break-all",
              }}
            >
              {importStatus}
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>

      {/* ── Setup & In-UI Engine Downloader ── */}
      <DownloadManager />
    </>
  );
};
