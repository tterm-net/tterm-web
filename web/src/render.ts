// Output frozen into text. What a finished command printed is drawn again by
// a terminal that is never shown, the same xterm.js, at the width the command
// ran at; then its buffer is read back cell by cell into HTML. So whatever
// the program did with the cursor (progress bars, colours, \r) comes out as
// the screen showed it, and the result can be selected, searched and
// copied like any text on the page.
//
// No inline styles: the page's security policy refuses them. Colours are
// classes. The sixteen basic ones are theme colours (--ansi-N in
// tokens.css), so a block frozen in the dark theme turns light with the page;
// the other 240 and any 24-bit colour get a class of their own, added once
// to a style element the first time they are seen.

import { Unicode11Addon } from "@xterm/addon-unicode11";
import {
  Terminal, type IBuffer, type IBufferCell, type IBufferLine,
} from "@xterm/xterm";

export interface Line {
  html: string;
  text: string;
}

export interface Rendered {
  lines: Line[];
  /** The output was longer than a block keeps: earlier lines are gone. */
  limited: boolean;
}

/** Lines a frozen block keeps, at most. */
export const KEEP_LINES = 5000;
/** Rows of the unseen terminal: room for programs that move the cursor up
 * over a few lines, as progress displays do. */
const ROWS = 50;

let sheet: HTMLStyleElement | null = null;
const known = new Set<string>();

/** A class for a colour that has no fixed one yet. */
function colourClass(prefix: "f" | "b" | "x" | "y", key: string,
  value: string): string {
  const name = `${prefix}${key}`;
  if (!known.has(name)) {
    known.add(name);
    if (!sheet) {
      // styleNonce.ts gives every style element made here the page's
      // one-time number, which the policy lets through.
      sheet = document.createElement("style");
      document.head.appendChild(sheet);
    }
    const prop = prefix === "f" || prefix === "x" ? "color" : "background-color";
    sheet.sheet?.insertRule(`.out .${name}{${prop}:${value}}`,
      sheet.sheet.cssRules.length);
  }
  return name;
}

function rgb256(i: number): string {
  if (i >= 232) {
    const v = 8 + (i - 232) * 10;
    return `rgb(${v},${v},${v})`;
  }
  const n = i - 16;
  const step = (x: number) => (x === 0 ? 0 : 55 + x * 40);
  return `rgb(${step(Math.floor(n / 36))},${step(Math.floor(n / 6) % 6)},` +
    `${step(n % 6)})`;
}

function fgClass(cell: IBufferCell, bold: boolean): string | null {
  if (cell.isFgRGB()) {
    const hex = cell.getFgColor().toString(16).padStart(6, "0");
    return colourClass("x", hex, `#${hex}`);
  }
  if (cell.isFgPalette()) {
    let i = cell.getFgColor();
    // As in the terminal itself: bold text in the eight basic colours is
    // drawn in their bright versions.
    if (bold && i < 8) i += 8;
    return i < 16 ? `f${i}` : colourClass("f", String(i), rgb256(i));
  }
  return null;
}

function bgClass(cell: IBufferCell): string | null {
  if (cell.isBgRGB()) {
    const hex = cell.getBgColor().toString(16).padStart(6, "0");
    return colourClass("y", hex, `#${hex}`);
  }
  if (cell.isBgPalette()) {
    const i = cell.getBgColor();
    return i < 16 ? `b${i}` : colourClass("b", String(i), rgb256(i));
  }
  return null;
}

/** The classes of one cell, as a string to compare and to write out. */
function look(cell: IBufferCell): string {
  const bold = !!cell.isBold();
  let fg = fgClass(cell, bold);
  let bg = bgClass(cell);
  if (cell.isInverse()) {
    // Swapped; the default colours swap too, into classes of their own.
    const f = bg ? bg.replace(/^b/, "f").replace(/^y/, "x") : "fbg";
    const b = fg ? fg.replace(/^f/, "b").replace(/^x/, "y") : "bfg";
    fg = f;
    bg = b;
  }
  const classes: string[] = [];
  if (bold) classes.push("ob");
  if (fg) classes.push(fg);
  if (bg) classes.push(bg);
  if (cell.isItalic()) classes.push("oi");
  if (cell.isDim()) classes.push("od");
  if (cell.isUnderline()) classes.push("ou");
  if (cell.isStrikethrough()) classes.push("os");
  if (cell.isInvisible()) classes.push("oh");
  return classes.join(" ");
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Where a line's text ends: past the last cell that is not a blank in the
 * default colours. */
function lineEnd(line: IBufferLine, cell: IBufferCell): number {
  for (let x = line.length - 1; x >= 0; x--) {
    line.getCell(x, cell);
    const chars = cell.getChars();
    if ((chars && chars !== " ") || !cell.isBgDefault() || cell.isInverse()) {
      return x + 1;
    }
  }
  return 0;
}

function appendLine(line: IBufferLine, cell: IBufferCell, end: number,
  out: { html: string[]; text: string[] }): void {
  let run = "";
  let runLook = "";
  const flush = () => {
    if (!run) return;
    const html = escapeHtml(run);
    out.html.push(runLook ? `<span class="${runLook}">${html}</span>` : html);
    run = "";
  };
  for (let x = 0; x < end; x++) {
    line.getCell(x, cell);
    if (cell.getWidth() === 0) continue;   // the right half of a wide one
    const chars = cell.getChars() || " ";
    const l = look(cell);
    if (l !== runLook) {
      flush();
      runLook = l;
    }
    run += chars;
    out.text.push(chars);
  }
  flush();
}

/** A terminal's buffer read back as lines of HTML. `full`: the buffer has
 * held as many lines as it keeps, so the first ones may be gone. */
export function readBuffer(buffer: IBuffer, full: boolean): Rendered {
  const cell = buffer.getNullCell();
  const lines: Line[] = [];
  let y = 0;
  while (y < buffer.length) {
    const parts = { html: [] as string[], text: [] as string[] };
    // A line the terminal wrapped is one line here; the page wraps it again
    // at whatever width it has.
    let line = buffer.getLine(y);
    while (line) {
      const next = buffer.getLine(y + 1);
      const wraps = !!next?.isWrapped;
      appendLine(line, cell, wraps ? line.length : lineEnd(line, cell), parts);
      y += 1;
      if (!wraps) break;
      line = next;
    }
    lines.push({ html: parts.html.join(""), text: parts.text.join("") });
  }
  while (lines.length && !lines[lines.length - 1].text.trim()) lines.pop();
  return { lines, limited: full };
}

/** The output of a command, drawn and read back. `cols` is the width it
 * ran at. */
export function renderOutput(data: Uint8Array, cols: number,
  keep = KEEP_LINES): Promise<Rendered> {
  const t = new Terminal({
    cols: Math.max(10, Math.min(1000, cols || 80)),
    rows: ROWS,
    scrollback: keep,
    allowProposedApi: true,
  });
  t.loadAddon(new Unicode11Addon());
  t.unicode.activeVersion = "11";
  return new Promise((resolve) => {
    t.write(data, () => {
      const buffer = t.buffer.normal;
      const rendered = readBuffer(buffer, buffer.length >= keep + ROWS);
      t.dispose();
      resolve(rendered);
    });
  });
}

/** The plain text of rendered lines, for the clipboard. */
export function plainText(rendered: Rendered): string {
  return rendered.lines.map((l) => l.text.replace(/\s+$/, "")).join("\n");
}

/** Bytes from base64, as the core sends a block's output in JSON. */
export function fromBase64(text: string): Uint8Array {
  const raw = atob(text);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  return bytes;
}
