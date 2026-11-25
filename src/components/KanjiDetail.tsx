import { FC } from "react";
import type { KanjiInfo } from "../utils/types";

interface KanjiDetailProps {
  kanjiList: KanjiInfo[];
}

export const KanjiDetail: FC<KanjiDetailProps> = ({ kanjiList }) => {
  if (!kanjiList || kanjiList.length === 0) return null;

  return (
    <div
      style={{
        marginTop: "8px",
        paddingTop: "8px",
        borderTop: "1px solid rgba(255, 255, 255, 0.08)",
      }}
    >
      <div
        style={{
          fontSize: "11px",
          fontWeight: "bold",
          marginBottom: "6px",
          opacity: 0.8,
          textTransform: "uppercase",
          letterSpacing: "0.5px",
        }}
      >
        漢字 Kanji Breakdown
      </div>

      {kanjiList.map((kanji, idx) => (
        <div
          key={idx}
          style={{
            display: "flex",
            gap: "10px",
            alignItems: "flex-start",
            marginBottom: "6px",
            padding: "6px 8px",
            background: "rgba(255, 255, 255, 0.03)",
            borderRadius: "4px",
          }}
        >
          {/* Large kanji character */}
          <div
            style={{
              fontSize: "28px",
              lineHeight: "1",
              minWidth: "36px",
              textAlign: "center",
            }}
          >
            {kanji.character}
          </div>

          {/* Details */}
          <div style={{ flex: 1, fontSize: "11px" }}>
            {/* Meanings */}
            {kanji.meanings?.length > 0 && (
              <div style={{ marginBottom: "2px" }}>
                <span style={{ opacity: 0.5 }}>Meaning: </span>
                {kanji.meanings.slice(0, 4).join(", ")}
              </div>
            )}

            {/* On'yomi */}
            {kanji.on_readings?.length > 0 && (
              <div style={{ marginBottom: "2px" }}>
                <span style={{ opacity: 0.5 }}>音: </span>
                <span style={{ color: "#e8a87c" }}>
                  {kanji.on_readings.slice(0, 4).join("、")}
                </span>
              </div>
            )}

            {/* Kun'yomi */}
            {kanji.kun_readings?.length > 0 && (
              <div>
                <span style={{ opacity: 0.5 }}>訓: </span>
                <span style={{ color: "#85c88a" }}>
                  {kanji.kun_readings.slice(0, 4).join("、")}
                </span>
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};
