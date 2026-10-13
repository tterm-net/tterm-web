// The terminal's colours, one set per theme, made from the design system:
// the page's ground behind the text, amber for the cursor, and the sixteen
// colours programs ask for (htop, ls, git) tuned to sit on that ground. In
// the light theme the colours are darker, so text keeps its contrast, and
// "white" is grey: on a white ground, white text would vanish.

import type { ITheme } from "@xterm/xterm";
import type { Shown } from "./theme";

const dark: ITheme = {
  background: "#0D0E11",
  foreground: "#EDEFF3",
  cursor: "#E9A33F",
  cursorAccent: "#0D0E11",
  selectionBackground: "rgba(108, 140, 255, .32)",
  scrollbarSliderBackground: "rgba(154, 160, 170, .22)",
  scrollbarSliderHoverBackground: "rgba(154, 160, 170, .36)",
  scrollbarSliderActiveBackground: "rgba(154, 160, 170, .5)",
  black: "#1D2026",
  red: "#E5534B",
  green: "#46B95C",
  yellow: "#E9A33F",
  blue: "#6C8CFF",
  magenta: "#C47BE0",
  cyan: "#4CB8C8",
  white: "#C9CDD4",
  brightBlack: "#7D838E",
  brightRed: "#F2736B",
  brightGreen: "#65D27A",
  brightYellow: "#F2BD69",
  brightBlue: "#93A9FF",
  brightMagenta: "#D89CEC",
  brightCyan: "#73D0DD",
  brightWhite: "#FFFFFF",
};

const light: ITheme = {
  background: "#FCFCFD",
  foreground: "#15171C",
  cursor: "#E9A33F",
  cursorAccent: "#15171C",
  selectionBackground: "rgba(71, 102, 230, .22)",
  scrollbarSliderBackground: "rgba(84, 91, 103, .22)",
  scrollbarSliderHoverBackground: "rgba(84, 91, 103, .36)",
  scrollbarSliderActiveBackground: "rgba(84, 91, 103, .5)",
  black: "#15171C",
  red: "#B3302A",
  green: "#15702F",
  yellow: "#93560A",
  blue: "#3554D1",
  magenta: "#8E3BA8",
  cyan: "#0B6F80",
  white: "#6B7280",
  brightBlack: "#5F6673",
  brightRed: "#C9443B",
  brightGreen: "#1E8A3E",
  brightYellow: "#A8650D",
  brightBlue: "#4766E6",
  brightMagenta: "#A452C2",
  brightCyan: "#12879B",
  brightWhite: "#9AA0AA",
};

export function terminalTheme(shown: Shown): ITheme {
  return shown === "light" ? light : dark;
}

/** The running block in the feed: its own quiet ground, and output in the
 * dimmer text colour the finished blocks use too (--live and --dim). */
export function liveTheme(shown: Shown): ITheme {
  return shown === "light"
    ? { ...light, background: "#F6F7F9", foreground: "#545B67" }
    : { ...dark, background: "#121419", foreground: "#9AA0AA" };
}
