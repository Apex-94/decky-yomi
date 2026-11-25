import { useEffect, useState, FC } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  Focusable,
} from "@decky/ui";
import { call } from "../utils/diagnostics";
import { toaster } from "@decky/api";
import type { VocabEntry } from "../utils/types";

export const VocabList: FC = () => {
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const [vocab, setVocab] = useState<VocabEntry[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [exportPath, setExportPath] = useState<string | null>(null);

  const loadVocab = async () => {
    setLoading(true);
    setError("");
    try {
      const [list, count] = await Promise.all([
        call<[number, number], VocabEntry[]>("get_vocab_list", 50, page * 50),
        call<[], number>("get_vocab_count"),
      ]);
      setVocab(list);
      setTotalCount(count);
    } catch (e) {
      setError("Could not load vocabulary: " + String(e));
    }
    setLoading(false);
  };

  useEffect(() => {
    loadVocab();
  }, [page]);

  const handleDelete = async (id: number) => {
    try {
      await call<[number], any>("delete_word", id);
      // Optimistic removal from UI
      setVocab((prev) => prev.filter((v) => v.id !== id));
      setTotalCount((prev) => prev - 1);
    } catch (e) {
      setError("Could not delete word: " + String(e));
    }
  };

  const handleExport = async (spreadsheetSafe: boolean = false) => {
    setExporting(true);
    try {
      const path = await call<[boolean], string>("export_vocab_csv", spreadsheetSafe);
      setExportPath(path);
      try {
        toaster.toast({
          title: "Decky Yomi",
          body: `Exported to ${path.split("/").pop() || "TSV"}`,
          duration: 3500,
        });
      } catch {
        // Ignore if toaster not active
      }
    } catch (e) {
      setError("Could not export: " + String(e));
    }
    setExporting(false);
  };

  return (
    <>
      {error && <PanelSection><div role="alert">{error}</div></PanelSection>}
      <PanelSection><ButtonItem disabled={page === 0 || loading} onClick={() => setPage(page - 1)}>Previous page</ButtonItem><ButtonItem disabled={(page + 1) * 50 >= totalCount || loading} onClick={() => setPage(page + 1)}>Next page</ButtonItem></PanelSection>
      {/* Header & Actions */}
      <PanelSection title={`📚 Vocabulary (${totalCount} words)`}>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => handleExport(false)}
            disabled={exporting || totalCount === 0}
          >
            {exporting ? "⏳ Exporting..." : "📤 Export to TSV (Anki)"}
          </ButtonItem>
        </PanelSectionRow>

        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => handleExport(true)}
            disabled={exporting || totalCount === 0}
          >
            {exporting ? "⏳ Exporting..." : "🛡️ Export Spreadsheet-Safe TSV"}
          </ButtonItem>
        </PanelSectionRow>

        {exportPath && (
          <PanelSectionRow>
            <div
              style={{
                fontSize: "11px",
                padding: "6px 10px",
                background: "rgba(100, 200, 100, 0.1)",
                borderRadius: "6px",
                color: "#85c88a",
              }}
            >
              ✅ Exported to:
              <br />
              <code style={{ fontSize: "10px", opacity: 0.8 }}>
                {exportPath}
              </code>
            </div>
          </PanelSectionRow>
        )}

        <PanelSectionRow>
          <ButtonItem layout="below" onClick={loadVocab} disabled={loading}>
            🔄 Refresh
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      {/* Word List */}
      <PanelSection>
        {loading ? (
          <PanelSectionRow>
            <div style={{ textAlign: "center", opacity: 0.5, padding: "16px" }}>
              Loading vocabulary...
            </div>
          </PanelSectionRow>
        ) : vocab.length === 0 ? (
          <PanelSectionRow>
            <div
              style={{
                textAlign: "center",
                opacity: 0.4,
                padding: "24px 8px",
                fontSize: "13px",
              }}
            >
              No saved words yet.
              <br />
              <br />
              Capture text and tap <b>"Save to Vocabulary"</b> on any word
              to start building your list!
            </div>
          </PanelSectionRow>
        ) : (
          vocab.map((entry) => (
            <Focusable key={entry.id}>
              <PanelSectionRow>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "6px 0",
                  }}
                >
                  {/* Word info */}
                  <div style={{ flex: 1 }}>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "baseline",
                        gap: "6px",
                      }}
                    >
                      <span style={{ fontSize: "15px", fontWeight: "bold" }}>
                        {entry.word}
                      </span>
                      {entry.reading && (
                        <span style={{ fontSize: "11px", opacity: 0.5 }}>
                          {entry.reading}
                        </span>
                      )}
                    </div>
                    <div
                      style={{
                        fontSize: "11px",
                        opacity: 0.7,
                        marginTop: "2px",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        maxWidth: "200px",
                      }}
                    >
                      {entry.meaning}
                    </div>
                    <div style={{ fontSize: "9px", opacity: 0.35, marginTop: "2px" }}>
                      {entry.game_name && `🎮 ${entry.game_name} • `}
                      {new Date(entry.created_at).toLocaleDateString()}
                    </div>
                  </div>

                  {/* Delete button */}
                  <ButtonItem
                    onClick={() => handleDelete(entry.id)}

                  >
                    Delete
                  </ButtonItem>
                </div>
              </PanelSectionRow>
            </Focusable>
          ))
        )}
      </PanelSection>
    </>
  );
};
