import type { WordInfo } from "./types";

export function surfaceReading({ surface, reading }: WordInfo): string {
  const toHiragana = (text: string) => text.replace(/[ァ-ヶヽヾ]/g,
    kana => String.fromCharCode(kana.charCodeAt(0) - 0x60));
  const annotation = toHiragana(reading);
  return annotation === toHiragana(surface) ? "" : annotation;
}
