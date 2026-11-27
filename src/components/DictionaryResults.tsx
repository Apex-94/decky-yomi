import { surfaceReading } from "../utils/readings";
import { useState, useEffect, FC } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  Focusable,
} from "@decky/ui";
import { call } from "../utils/diagnostics";
import { KanjiDetail } from "./KanjiDetail";
import type { AnalysisResult, Settings } from "../utils/types";

import { entryChoices, saveChoice, type EntryChoice } from "../utils/vocabulary";

interface DictionaryResultsProps {
  result: AnalysisResult | null;
  onSaveWord?: () => void;
}

export const DictionaryResults: FC<DictionaryResultsProps> = ({
  result,
  onSaveWord,
}) => {
  const [expandedWord, setExpandedWord] = useState<number | null>(null);
  const [savedWords, setSavedWords] = useState<Set<string>>(new Set());

  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  useEffect(() => { call<[], Settings>("get_settings").then(setSettings).catch(() => {}); }, []);
  useEffect(() => { setSavedWords(new Set()); setExpandedWord(null); }, [result]);

  // ── Empty state ──
  if (!result) {
    return (
      <PanelSection title="📖 Results">
        <PanelSectionRow>
          <div
            style={{
              opacity: 0.4,
              textAlign: "center",
              padding: "24px 8px",
              fontSize: "13px",
            }}
          >
            No results yet.
            <br />
            Use the OCR tab to capture Japanese text.
          </div>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  // ── Error state ──
  if (result.error) {
    return (
      <PanelSection title="❌ Error">
        <PanelSectionRow>
          <div
            style={{
              color: "#ff6b6b",
              padding: "8px",
              fontSize: "13px",
              background: "rgba(255, 107, 107, 0.1)",
              borderRadius: "6px",
            }}
          >
            {result.error}
          </div>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  // ── Save word handler ──
  const handleSaveWord = async (choice: EntryChoice, key: string) => {
    setSaving(true);
    setError("");
    try {
      await saveChoice(choice, result.raw_text);
      setSavedWords(prev => new Set(prev).add(key));
      onSaveWord?.();
    } catch (e) { setError("Could not save word: " + String(e)); }
    finally { setSaving(false); }
  };

  return (
    <>
      {error && <PanelSection><div role="alert">{error}</div></PanelSection>}
      {/* ── Raw OCR Text ── */}
      <PanelSection title="📝 Detected Text">
        <PanelSectionRow>
          <div
            style={{
              fontSize: "15px",
              lineHeight: "1.7",
              padding: "10px 12px",
              background: "rgba(255, 255, 255, 0.05)",
              borderRadius: "8px",
              wordBreak: "break-word",
            }}
          >
            {result.raw_text || "(empty)"}
          </div>
        </PanelSectionRow>
      </PanelSection>

      {/* ── Word-by-Word Breakdown ── */}
      <PanelSection title={`📖 Words (${result.words?.filter((w) => !w.skipped).length ?? 0})`}>
        {result.words?.map((wordResult, idx) => {
          if (wordResult.skipped) return null;

          const word = wordResult.word;
          const lookup = wordResult.lookup;
          const isExpanded = expandedWord === idx;
          const firstEntry = lookup?.entries?.[0];
          const choices = entryChoices(wordResult);

          return (
            <Focusable key={idx}>
              {/* ── Word Summary Row ── */}
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  onClick={() => setExpandedWord(isExpanded ? null : idx)}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "baseline",
                    }}
                  >
                    {/* Surface form */}
                    <span style={{ fontSize: "17px", fontWeight: "bold" }}>
                      {word.surface}
                    </span>
                    {/* Reading */}
                    <span style={{ fontSize: "12px", opacity: 0.6 }}>
                      {settings?.show_readings !== false ? surfaceReading(word) : ""}
                    </span>
                  </div>

                  {/* First meaning preview */}
                  {firstEntry && (
                    <div
                      style={{
                        fontSize: "12px",
                        opacity: 0.75,
                        marginTop: "2px",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {firstEntry.senses?.[0]?.glosses?.slice(0, 3).join(", ")}
                    </div>
                  )}

                  {/* No results indicator */}
                  {!firstEntry && (
                    <div style={{ fontSize: "11px", opacity: 0.4, marginTop: "2px" }}>
                      No dictionary entry found
                    </div>
                  )}
                </ButtonItem>
              </PanelSectionRow>

              {/* ── Expanded Detail ── */}
              {isExpanded && lookup && (
                <div
                  style={{
                    padding: "10px 12px",
                    margin: "0 8px 8px 8px",
                    background: "rgba(0, 0, 0, 0.25)",
                    borderRadius: "6px",
                    fontSize: "13px",
                  }}
                >
                  {/* POS tag */}
                  {settings?.show_pos !== false && word.pos && word.pos !== "unknown" && (
                    <div
                      style={{
                        display: "inline-block",
                        padding: "2px 6px",
                        borderRadius: "3px",
                        background: "rgba(100, 180, 255, 0.15)",
                        color: "#8cc4ff",
                        fontSize: "10px",
                        marginBottom: "8px",
                      }}
                    >
                      {word.pos}
                    </div>
                  )}

                  {/* All entries & senses */}
                  {wordResult.components && <p>{wordResult.components.map(p => p.word.surface).join(" + ")}</p>}
                  {choices.map((choice, eIdx) => {
                    const { entry } = choice;
                    const key = idx + "/" + eIdx;
                    return (
                      <div
                        key={eIdx}
                        style={{
                          marginBottom: "10px",
                          paddingBottom: "8px",
                          borderBottom:
                            eIdx < choices.length - 1
                              ? "1px solid rgba(255,255,255,0.06)"
                              : "none",
                        }}
                      >
                        {/* Kanji + Kana heading */}
                        <div style={{ fontWeight: "bold", fontSize: "14px" }}>
                          {choice.word !== word && <small>{choice.word.surface} → </small>}
                          {entry.headword || entry.kanji[0] || entry.kana[0]}
                          {settings?.show_readings !== false && entry.reading && (
                            <span> 【{entry.reading}】</span>
                          )}
                        </div>

                        {/* Senses */}
                        {entry.senses?.map((sense, sIdx) => (
                          <div
                            key={sIdx}
                            style={{
                              marginLeft: "12px",
                              marginTop: "3px",
                              lineHeight: "1.4",
                            }}
                          >
                            <span style={{ color: "#888", fontSize: "11px" }}>
                              {sIdx + 1}.{" "}
                            </span>
                            {settings?.show_pos !== false && sense.pos?.length > 0 && (
                              <span
                                style={{
                                  color: "#6ba3d6",
                                  fontSize: "10px",
                                  fontStyle: "italic",
                                }}
                              >
                                [{sense.pos.join(", ")}]{" "}
                              </span>
                            )}
                            <span style={{ whiteSpace: "pre-line" }}>{sense.glosses?.join("; ")}</span>
                          </div>
                        ))}
                        <ButtonItem layout="below" onClick={() => handleSaveWord(choice, key)}
                          disabled={savedWords.has(key) || saving}>
                          {savedWords.has(key) ? "✓ Saved" : "Save this entry"}
                        </ButtonItem>
                      </div>
                    );
                  })}

                  {/* Kanji breakdown */}
                  {lookup.kanji?.length > 0 && (
                    <KanjiDetail kanjiList={lookup.kanji} />
                  )}

                  {choices.length === 0 && <ButtonItem layout="below" disabled={saving || savedWords.has(String(idx))}
                    onClick={() => handleSaveWord({ word, entry: { kanji: [], kana: [], senses: [], id: null } }, String(idx))}>
                    {savedWords.has(String(idx)) ? "✓ Saved" : "Save word without definition"}
                  </ButtonItem>}
                </div>
              )}
            </Focusable>
          );
        })}
      </PanelSection>
    </>
  );
};
