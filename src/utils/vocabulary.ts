import { call } from "./diagnostics";
import type { DictEntry, WordInfo, WordResult } from "./types";

export interface EntryChoice {
  word: WordInfo;
  entry: DictEntry;
}

export function entryChoices(result?: WordResult): EntryChoice[] {
  if (!result) return [];
  return [result, ...(result.components ?? [])].flatMap(part =>
    (part.lookup?.entries ?? []).map(entry => ({ word: part.word, entry }))
  );
}

export function cardFields({ word, entry }: EntryChoice, sentence: string): [string, string, string, string, string, string] {
  return [
    entry.headword ?? entry.kanji[0] ?? entry.kana[0] ?? word.surface,
    entry.reading ?? entry.kana[0] ?? word.reading,
    entry.senses.map(s => s.glosses.join(", ")).join("; "),
    word.pos, sentence, "",
  ];
}

export async function saveChoice(choice: EntryChoice, sentence: string) {
  const fields = cardFields(choice, sentence);
  const result = await call<typeof fields, { saved: boolean; error?: string }>("save_word", ...fields);
  if (!result.saved) throw new Error(result.error || "Could not save vocabulary");
}
