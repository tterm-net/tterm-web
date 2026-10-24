// The words search found, marked where they are (0.1.7): in text the page
// draws itself, and in a block's output, which comes drawn as HTML.

import type { ReactNode } from "react";

/** Where each of `words` is in `text`, as [start, end) pairs in order, no
 * two overlapping. */
export function spans(text: string, words: string[]): [number, number][] {
  const low = text.toLowerCase();
  const found: [number, number][] = [];
  for (const word of words) {
    if (!word) continue;
    let at = low.indexOf(word);
    while (at >= 0) {
      found.push([at, at + word.length]);
      at = low.indexOf(word, at + word.length);
    }
  }
  found.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  const merged: [number, number][] = [];
  for (const [start, end] of found) {
    const last = merged[merged.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  return merged;
}

/** The text with the words in <mark>. */
export function marked(text: string, words: string[], className: string): ReactNode {
  const parts = spans(text, words);
  if (!parts.length) return text;
  const out: ReactNode[] = [];
  let at = 0;
  parts.forEach(([start, end], i) => {
    if (start > at) out.push(text.slice(at, start));
    out.push(<mark key={i} className={className}>{text.slice(start, end)}</mark>);
    at = end;
  });
  if (at < text.length) out.push(text.slice(at));
  return out;
}

/** Marks the words in what is written inside `root`, an output drawn as
 * HTML: each piece of text found goes into a <mark>. A word split between
 * two colours is left as it is. Returns how many were marked. */
export function markIn(root: HTMLElement, words: string[], className: string,
  most = 200): number {
  if (!words.length) return 0;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if ((node.parentElement?.closest("mark") ?? null) === null) nodes.push(node as Text);
  }
  let count = 0;
  for (const node of nodes) {
    if (count >= most) break;
    const text = node.data;
    const parts = spans(text, words);
    if (!parts.length) continue;
    const pieces = document.createDocumentFragment();
    let at = 0;
    for (const [start, end] of parts) {
      if (start > at) pieces.append(text.slice(at, start));
      const mark = document.createElement("mark");
      mark.className = className;
      mark.textContent = text.slice(start, end);
      pieces.append(mark);
      at = end;
      count += 1;
    }
    if (at < text.length) pieces.append(text.slice(at));
    node.replaceWith(pieces);
  }
  return count;
}

/** In an output marked by markIn, the first mark on the first line that
 * has every word, as search found it: where to look in a long output. Lines
 * are what the text has between line breaks. */
export function markOnLine(root: HTMLElement, words: string[],
  className: string): HTMLElement | null {
  const first = root.querySelector<HTMLElement>(`mark.${className}`);
  if (!first) return null;
  let start = 0;
  let line: [number, number] | null = null;
  for (const text of (root.textContent ?? "").toLowerCase().split("\n")) {
    if (words.every((w) => text.includes(w))) {
      line = [start, start + text.length];
      break;
    }
    start += text.length + 1;
  }
  if (!line) return first;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let at = 0;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (at > line[1]) break;
    const mark = node.parentElement?.closest<HTMLElement>(`mark.${className}`);
    if (mark && at >= line[0]) return mark;
    at += (node as Text).data.length;
  }
  return first;
}
