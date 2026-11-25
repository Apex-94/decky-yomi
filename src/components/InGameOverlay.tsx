import { FC, useState, useEffect, useRef, useCallback } from "react";
import { findModuleChild, showModal, ModalRoot, Focusable, findSP } from "@decky/ui";
import type { ShowModalResult } from "@decky/ui";
import { call } from "@decky/api";
import { shortcutManager, OverlayState, OverlayAction } from "../utils/shortcutManager";
import type { WordResult } from "../utils/types";

enum UIComposition {
  Hidden = 0,
  Notification = 1,
  Overlay = 2,
  Opaque = 3,
  OverlayKeyboard = 4,
}

// Find Steam's internal composition request hook to surface CEF over Gamescope
const useUIComposition: ((composition: UIComposition) => void) | undefined =
  typeof findModuleChild === "function"
    ? findModuleChild((m: any) => {
        if (typeof m !== "object" || !m) return undefined;
        for (const prop in m) {
          const fn = m[prop];
          if (
            typeof fn === "function" &&
            fn.toString().includes("AddMinimumCompositionStateRequest") &&
            fn.toString().includes("ChangeMinimumCompositionStateRequest") &&
            fn.toString().includes("RemoveMinimumCompositionStateRequest") &&
            !fn.toString().includes("m_mapCompositionStateRequests")
          ) {
            return fn;
          }
        }
      })
    : undefined;

const CompositionRequest: FC<{ level: UIComposition }> = ({ level }) => {
  if (useUIComposition) {
    useUIComposition(level);
  }
  return null;
};

const OverlayResultsCard: FC = () => {
  const [state, setState] = useState<OverlayState>(shortcutManager.getState());
  const [selectedWordIdx, setSelectedWordIdx] = useState<number>(0);
  const [selectedEntryIdx, setSelectedEntryIdx] = useState<number>(0);
  const [savingWord, setSavingWord] = useState<boolean>(false);
  const [savedSet, setSavedSet] = useState<Set<number>>(new Set());
  const chipRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    return shortcutManager.subscribe((newState) => {
      setState(newState);
      if (newState.result?.words && newState.result.words.length > 0) {
        const firstDef = newState.result.words.findIndex(
          (w) => !w.skipped && w.lookup?.entries && w.lookup.entries.length > 0
        );
        setSelectedWordIdx(firstDef >= 0 ? firstDef : 0);
        setSelectedEntryIdx(0);
        setSavedSet(new Set());
      }
    });
  }, []);

  useEffect(() => {
    setSelectedEntryIdx(0);
    const el = chipRefs.current[selectedWordIdx];
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }, [selectedWordIdx]);

  const words = state.result?.words || [];
  const selectedWord = words[selectedWordIdx];
  const entries = selectedWord?.lookup?.entries || [];
  const safeEntryIdx = Math.min(selectedEntryIdx, Math.max(0, entries.length - 1));
  const selectedEntry = entries[safeEntryIdx];

  const handleSaveCurrentWord = useCallback(async (wordResult: WordResult, idx: number, entryIdx = 0) => {
    const entry = wordResult.lookup?.entries?.[entryIdx] ?? wordResult.lookup?.entries?.[0];
    const meaning =
      entry?.senses?.map((s) => s.glosses.join(", ")).join("; ") ?? "";

    setSavingWord(true);
    try {
      await call(
        "save_word",
        wordResult.word.surface,
        entry?.kana?.[0] ?? wordResult.word.reading ?? "",
        meaning,
        wordResult.word.pos ?? "",
        state.result?.raw_text ?? "",
        ""
      );
      setSavedSet((prev) => new Set(prev).add(idx));
    } catch (e) {
      console.error("Save word failed", e);
    }
    setSavingWord(false);
  }, [state.result?.raw_text]);

  const navContextRef = useRef({
    words,
    selectedWordIdx,
    safeEntryIdx,
    entriesLen: entries.length,
    savingWord,
    savedSet,
    handleSaveCurrentWord,
  });
  navContextRef.current = {
    words,
    selectedWordIdx,
    safeEntryIdx,
    entriesLen: entries.length,
    savingWord,
    savedSet,
    handleSaveCurrentWord,
  };

  useEffect(() => {
    return shortcutManager.onAction((action: OverlayAction) => {
      const ctx = navContextRef.current;
      const len = ctx.words.length;
      if (action === "dismiss") {
        shortcutManager.dismiss();
        return;
      }
      if (action === "recapture") {
        shortcutManager.triggerCapture();
        return;
      }
      if (!len) return;

      const stepMatching = (dir: 1 | -1, requireLookup: boolean) => {
        for (let offset = 1; offset <= len; offset++) {
          const candidate = (ctx.selectedWordIdx + dir * offset + len) % len;
          const w = ctx.words[candidate];
          const hasLookup = !w.skipped && (w.lookup?.entries?.length ?? 0) > 0;
          if (requireLookup ? hasLookup : !w.skipped) {
            setSelectedWordIdx(candidate);
            return;
          }
        }
        setSelectedWordIdx((ctx.selectedWordIdx + dir + len) % len);
      };

      switch (action) {
        case "prev_word":
          stepMatching(-1, false);
          break;
        case "next_word":
          stepMatching(1, false);
          break;
        case "prev_defined":
          stepMatching(-1, true);
          break;
        case "next_defined":
          stepMatching(1, true);
          break;
        case "prev_entry":
          if (ctx.entriesLen > 1) {
            setSelectedEntryIdx((ctx.safeEntryIdx - 1 + ctx.entriesLen) % ctx.entriesLen);
          }
          break;
        case "next_entry":
          if (ctx.entriesLen > 1) {
            setSelectedEntryIdx((ctx.safeEntryIdx + 1) % ctx.entriesLen);
          }
          break;
        case "save_word": {
          const w = ctx.words[ctx.selectedWordIdx];
          if (w && (w.lookup?.entries?.length ?? 0) > 0 && !ctx.savingWord && !ctx.savedSet.has(ctx.selectedWordIdx)) {
            void ctx.handleSaveCurrentWord(w, ctx.selectedWordIdx, ctx.safeEntryIdx);
          }
          break;
        }
      }
    });
  }, []);

  if (!state.visible) return null;

  return (
    <div
      style={{
        width: "880px",
        maxWidth: "90vw",
        backgroundColor: "rgba(16, 20, 28, 0.96)",
        color: "#FFFFFF",
        borderRadius: "14px",
        border: "1px solid rgba(255, 255, 255, 0.16)",
        boxShadow: "0 16px 40px rgba(0, 0, 0, 0.85)",
        backdropFilter: "blur(14px)",
        padding: "16px 20px",
        fontFamily: "'Segoe UI', 'Noto Sans CJK JP', sans-serif",
        boxSizing: "border-box",
      }}
    >
      {state.loading && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "10px 4px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <span style={{ fontSize: "22px" }}>🔍</span>
            <div>
              <div style={{ fontSize: "16px", fontWeight: "bold" }}>
                Decky Yomi
              </div>
              <div style={{ fontSize: "13px", opacity: 0.7 }}>
                {state.loadingMessage || "Reading Japanese dialogue..."}
              </div>
            </div>
          </div>
          <button
            onClick={() => shortcutManager.dismiss()}
            style={{
              background: "rgba(255, 255, 255, 0.1)",
              border: "none",
              color: "#FFFFFF",
              padding: "6px 14px",
              borderRadius: "6px",
              cursor: "pointer",
              fontSize: "13px",
            }}
          >
            Cancel
          </button>
        </div>
      )}

      {!state.loading && state.error && (
        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "8px",
            }}
          >
            <div style={{ color: "#ff6b6b", fontWeight: "bold", fontSize: "15px" }}>
              ⚠️ Capture Error
            </div>
            <button
              onClick={() => shortcutManager.dismiss()}
              style={{
                background: "transparent",
                border: "none",
                color: "#FFFFFF",
                fontSize: "16px",
                cursor: "pointer",
              }}
            >
              ✕
            </button>
          </div>
          <div
            style={{
              fontSize: "13px",
              color: "rgba(255, 255, 255, 0.85)",
              background: "rgba(255, 107, 107, 0.15)",
              padding: "10px 12px",
              borderRadius: "8px",
            }}
          >
            {state.error}
          </div>
        </div>
      )}

      {!state.loading && !state.error && state.result && (
        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "flex-start",
              gap: "12px",
              borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
              paddingBottom: "10px",
              marginBottom: "12px",
            }}
          >
            <div style={{ fontSize: "20px", fontWeight: "600", color: "#f0f6fc", lineHeight: 1.3 }}>
              {state.result.raw_text}
            </div>
            <div style={{ display: "flex", gap: "8px", flexShrink: 0 }}>
              <button
                onClick={() => shortcutManager.triggerCapture()}
                style={{
                  background: "rgba(88, 166, 255, 0.18)",
                  border: "1px solid rgba(88, 166, 255, 0.45)",
                  color: "#79c0ff",
                  padding: "4px 10px",
                  borderRadius: "6px",
                  cursor: "pointer",
                  fontSize: "12px",
                  fontWeight: "bold",
                }}
              >
                🔄 Re-read
              </button>
              <button
                onClick={() => shortcutManager.dismiss()}
                style={{
                  background: "rgba(255, 255, 255, 0.12)",
                  border: "none",
                  color: "#FFFFFF",
                  padding: "4px 10px",
                  borderRadius: "6px",
                  cursor: "pointer",
                  fontSize: "12px",
                  fontWeight: "bold",
                }}
              >
                ✕ Dismiss
              </button>
            </div>
          </div>

          {words.length > 0 && (
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: "8px",
                marginBottom: "14px",
                maxHeight: "120px",
                overflowY: "auto",
              }}
            >
              {words.map((w, idx) => {
                const isSelected = selectedWordIdx === idx;
                const hasLookup =
                  !w.skipped && w.lookup?.entries && w.lookup.entries.length > 0;
                const reading =
                  w.lookup?.entries?.[0]?.kana?.[0] || w.word.reading || "";

                return (
                  <button
                    key={idx}
                    ref={(el) => { chipRefs.current[idx] = el; }}
                    onClick={() => {
                      shortcutManager.resetAutoDismiss();
                      setSelectedWordIdx(idx);
                    }}
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      background: isSelected
                        ? "rgba(56, 139, 253, 0.35)"
                        : hasLookup
                        ? "rgba(255, 255, 255, 0.08)"
                        : "rgba(255, 255, 255, 0.03)",
                      border: isSelected
                        ? "1.5px solid #58a6ff"
                        : "1px solid rgba(255, 255, 255, 0.1)",
                      borderRadius: "8px",
                      padding: "4px 10px",
                      cursor: "pointer",
                      color: isSelected ? "#ffffff" : hasLookup ? "#e6edf3" : "#8b949e",
                    }}
                  >
                    {reading && reading !== w.word.surface && (
                      <span style={{ fontSize: "10px", opacity: 0.8, lineHeight: 1 }}>
                        {reading}
                      </span>
                    )}
                    <span style={{ fontSize: "15px", fontWeight: isSelected ? "bold" : "normal" }}>
                      {w.word.surface}
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          {selectedWord && (
            <div
              style={{
                background: "rgba(0, 0, 0, 0.35)",
                borderRadius: "8px",
                padding: "10px 14px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: "16px",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: "10px", flexWrap: "wrap" }}>
                  <span style={{ fontSize: "18px", fontWeight: "bold", color: "#58a6ff" }}>
                    {selectedWord.word.surface}
                  </span>
                  {selectedEntry?.kana?.[0] && (
                    <span style={{ fontSize: "14px", opacity: 0.85 }}>
                      【{selectedEntry.kana[0]}】
                    </span>
                  )}
                  {selectedWord.word.pos && (
                    <span
                      style={{
                        fontSize: "11px",
                        background: "rgba(255, 255, 255, 0.12)",
                        padding: "2px 6px",
                        borderRadius: "4px",
                        color: "#8b949e",
                      }}
                    >
                      {selectedWord.word.pos}
                    </span>
                  )}
                  {entries.length > 1 && (
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "4px",
                        fontSize: "11px",
                        color: "#79c0ff",
                        background: "rgba(88, 166, 255, 0.15)",
                        padding: "1px 6px",
                        borderRadius: "4px",
                      }}
                    >
                      <button
                        onClick={() => setSelectedEntryIdx((safeEntryIdx - 1 + entries.length) % entries.length)}
                        style={{ background: "none", border: "none", color: "inherit", cursor: "pointer", padding: "0 2px" }}
                      >
                        ◀
                      </button>
                      Entry {safeEntryIdx + 1}/{entries.length}
                      <button
                        onClick={() => setSelectedEntryIdx((safeEntryIdx + 1) % entries.length)}
                        style={{ background: "none", border: "none", color: "inherit", cursor: "pointer", padding: "0 2px" }}
                      >
                        ▶
                      </button>
                    </span>
                  )}
                </div>

                <div style={{ marginTop: "4px", fontSize: "13px", color: "#d0d7de" }}>
                  {selectedEntry?.senses && selectedEntry.senses.length > 0 ? (
                    selectedEntry.senses.slice(0, 3).map((s, sIdx) => (
                      <div key={sIdx} style={{ lineHeight: 1.35 }}>
                        <span style={{ opacity: 0.6, marginRight: "4px" }}>
                          {sIdx + 1}.
                        </span>
                        {s.glosses.join(", ")}
                      </div>
                    ))
                  ) : (
                    <span style={{ fontStyle: "italic", opacity: 0.6 }}>
                      No direct dictionary entry found
                    </span>
                  )}
                </div>
              </div>

              {selectedEntry && (
                <button
                  disabled={savingWord || savedSet.has(selectedWordIdx)}
                  onClick={() => handleSaveCurrentWord(selectedWord, selectedWordIdx, safeEntryIdx)}
                  style={{
                    background: savedSet.has(selectedWordIdx)
                      ? "rgba(46, 160, 67, 0.3)"
                      : "#238636",
                    border: savedSet.has(selectedWordIdx)
                      ? "1px solid #2ea043"
                      : "none",
                    color: "#FFFFFF",
                    padding: "8px 14px",
                    borderRadius: "6px",
                    fontSize: "13px",
                    fontWeight: "bold",
                    cursor: savedSet.has(selectedWordIdx) ? "default" : "pointer",
                    flexShrink: 0,
                  }}
                >
                  {savedSet.has(selectedWordIdx)
                    ? "✓ Saved"
                    : savingWord
                    ? "Saving..."
                    : "💾 Save"}
                </button>
              )}
            </div>
          )}

          <div
            style={{
              marginTop: "10px",
              paddingTop: "8px",
              borderTop: "1px solid rgba(255, 255, 255, 0.08)",
              display: "flex",
              flexWrap: "wrap",
              gap: "12px",
              fontSize: "11px",
              color: "rgba(255, 255, 255, 0.65)",
            }}
          >
            <span>◀▶ Word</span>
            <span>L1/R1 Defined Word</span>
            <span>▲▼ Entry</span>
            <span>Ⓐ/Ⓧ Save</span>
            <span>Ⓨ Re-read</span>
            <span>Ⓑ Close</span>
          </div>
        </div>
      )}
    </div>
  );
};

const OverlayModalHost: FC<{ closeModal?: () => void }> = ({ closeModal }) => {
  const handleCancel = () => {
    shortcutManager.dismiss();
    closeModal?.();
  };

  return (
    <ModalRoot onCancel={handleCancel} closeModal={handleCancel} bAllowFullSize bHideCloseIcon>
      <Focusable
        preferredFocus
        noFocusRing
        onCancelButton={handleCancel}
        onButtonDown={(evt) => evt.stopPropagation()}
        style={{ display: "flex", justifyContent: "center" }}
      >
        <OverlayResultsCard />
      </Focusable>
    </ModalRoot>
  );
};

export const InGameOverlay: FC = () => {
  const [state, setState] = useState<OverlayState>(shortcutManager.getState());
  const modalRef = useRef<ShowModalResult | null>(null);
  const [modalActive, setModalActive] = useState<boolean>(false);

  useEffect(() => {
    return shortcutManager.subscribe((newState) => {
      setState(newState);
    });
  }, []);

  // Open/close native Steam Modal when interactive overlay becomes visible so ABXY/DPAD don't leak to game
  useEffect(() => {
    if (state.visible && !modalRef.current && typeof showModal === "function") {
      try {
        const targetWin = typeof findSP === "function" ? findSP() || window : window;
        modalRef.current = showModal(<OverlayModalHost />, targetWin, {
          strTitle: "Decky Yomi",
          bHideActionIcons: true,
          fnOnClose: () => {
            modalRef.current = null;
            setModalActive(false);
            if (shortcutManager.getState().visible) {
              shortcutManager.dismiss();
            }
          },
        });
        setModalActive(true);
      } catch (e) {
        console.warn("showModal fallback to inline overlay:", e);
        modalRef.current = null;
        setModalActive(false);
      }
    } else if (!state.visible && modalRef.current) {
      const handle = modalRef.current;
      modalRef.current = null;
      setModalActive(false);
      try {
        handle.Close();
      } catch {}
    }
  }, [state.visible]);

  useEffect(() => {
    return () => {
      if (modalRef.current) {
        try {
          modalRef.current.Close();
        } catch {}
        modalRef.current = null;
      }
    };
  }, []);

  const preview = state.regionPreview;
  const showInlineCard = state.visible && !modalActive;
  const isVisible = showInlineCard || state.holding || preview.visible;

  return (
    <>
      {isVisible && <CompositionRequest level={showInlineCard ? UIComposition.Overlay : UIComposition.Notification} />}

      {/* Live OCR Region Helper Lines Overlay */}
      {preview.visible && (
        <div
          id="decky-yomi-region-guide"
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 899990,
            pointerEvents: "none",
          }}
        >
          {/* Dimmed outer masks */}
          <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: `${preview.y}%`, background: "rgba(0, 0, 0, 0.42)" }} />
          <div style={{ position: "absolute", top: `${preview.y + preview.h}%`, left: 0, right: 0, bottom: 0, background: "rgba(0, 0, 0, 0.42)" }} />
          <div style={{ position: "absolute", top: `${preview.y}%`, left: 0, width: `${preview.x}%`, height: `${preview.h}%`, background: "rgba(0, 0, 0, 0.42)" }} />
          <div style={{ position: "absolute", top: `${preview.y}%`, left: `${preview.x + preview.w}%`, right: 0, height: `${preview.h}%`, background: "rgba(0, 0, 0, 0.42)" }} />

          {/* Active capture box with dashed border, corner brackets & alignment helper lines */}
          <div
            style={{
              position: "absolute",
              left: `${preview.x}%`,
              top: `${preview.y}%`,
              width: `${preview.w}%`,
              height: `${preview.h}%`,
              border: "2px dashed #58a6ff",
              boxShadow: "0 0 0 1px rgba(0, 0, 0, 0.7), inset 0 0 18px rgba(88, 166, 255, 0.14)",
              boxSizing: "border-box",
            }}
          >
            {/* Corner brackets */}
            <div style={{ position: "absolute", top: -2, left: -2, width: 16, height: 16, borderTop: "3px solid #79c0ff", borderLeft: "3px solid #79c0ff" }} />
            <div style={{ position: "absolute", top: -2, right: -2, width: 16, height: 16, borderTop: "3px solid #79c0ff", borderRight: "3px solid #79c0ff" }} />
            <div style={{ position: "absolute", bottom: -2, left: -2, width: 16, height: 16, borderBottom: "3px solid #79c0ff", borderLeft: "3px solid #79c0ff" }} />
            <div style={{ position: "absolute", bottom: -2, right: -2, width: 16, height: 16, borderBottom: "3px solid #79c0ff", borderRight: "3px solid #79c0ff" }} />

            {/* Horizontal alignment helper lines (thirds + center baseline) */}
            <div style={{ position: "absolute", left: 0, right: 0, top: "33.3%", borderTop: "1px dashed rgba(121, 192, 255, 0.45)" }} />
            <div style={{ position: "absolute", left: 0, right: 0, top: "50%", borderTop: "1px solid rgba(255, 215, 0, 0.55)" }} />
            <div style={{ position: "absolute", left: 0, right: 0, top: "66.6%", borderTop: "1px dashed rgba(121, 192, 255, 0.45)" }} />

            {/* Vertical alignment helper lines (thirds + center) */}
            <div style={{ position: "absolute", top: 0, bottom: 0, left: "33.3%", borderLeft: "1px dashed rgba(121, 192, 255, 0.35)" }} />
            <div style={{ position: "absolute", top: 0, bottom: 0, left: "50%", borderLeft: "1px solid rgba(255, 215, 0, 0.45)" }} />
            <div style={{ position: "absolute", top: 0, bottom: 0, left: "66.6%", borderLeft: "1px dashed rgba(121, 192, 255, 0.35)" }} />

            {/* Coordinate label badge */}
            <div
              style={{
                position: "absolute",
                top: preview.y > 6 ? "-26px" : "6px",
                left: "6px",
                background: "rgba(16, 20, 28, 0.92)",
                color: "#79c0ff",
                border: "1px solid rgba(88, 166, 255, 0.6)",
                borderRadius: "6px",
                padding: "2px 8px",
                fontSize: "12px",
                fontWeight: 600,
                whiteSpace: "nowrap",
              }}
            >
              📐 OCR Box: X {preview.x}% · Y {preview.y}% · {preview.w}% × {preview.h}%
            </div>
          </div>
        </div>
      )}

      <div
        id="decky-yomi-overlay-container"
        style={{
          position: "fixed",
          bottom: "36px",
          left: "50%",
          transform: "translateX(-50%)",
          zIndex: 899999,
          pointerEvents: showInlineCard ? "auto" : "none",
          opacity: showInlineCard || (state.holding && !state.visible) ? 1 : 0,
          transition: "opacity 0.15s ease",
          boxSizing: "border-box",
        }}
      >
        {/* Hold feedback badge */}
        {state.holding && !state.visible && (
          <div
            style={{
              background: "rgba(16, 20, 28, 0.95)",
              color: "#FFFFFF",
              borderRadius: "24px",
              padding: "10px 22px",
              display: "flex",
              alignItems: "center",
              gap: "12px",
              border: "1px solid rgba(88, 166, 255, 0.4)",
              boxShadow: "0 10px 30px rgba(0, 0, 0, 0.8)",
              fontSize: "14px",
              fontWeight: 600,
              letterSpacing: "0.2px",
            }}
          >
            <span style={{ fontSize: "16px" }}>📖</span>
            <span>Holding <strong>{state.triggerButton || "L4"}</strong> to read dialogue...</span>
          </div>
        )}

        {/* Fallback inline card if showModal is unavailable */}
        {showInlineCard && <OverlayResultsCard />}
      </div>
    </>
  );
};
