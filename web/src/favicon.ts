// The tab's icon (0.1.9): tTerm's own with a green dot in the top right
// corner, so that a tab of the web is not taken for the site's (the owner,
// 25.10; the site and tTerm for the computer keep the icon without it). The
// dot is 38% of the side, as Claude's is, with a cut round it through which
// the strip of tabs shows, light or dark: approved on the canvas, version 77.
//
// It blinks while a command runs in a terminal of this page, and it is a
// grey ring while the page has lost tTerm Web. A tab does not play an
// animation inside its icon, so the page swaps the icon itself; a tab behind
// is slowed down by the browser, and blinks more slowly there.

const LOGO = '<rect width="512" height="512" rx="116" fill="#131417"/>' +
  '<path d="M118 168 L206 256 L118 344" fill="none" stroke="#E9A33F" ' +
  'stroke-width="56" stroke-linecap="round" stroke-linejoin="round"/>' +
  '<path d="M330 140 L330 316 C330 344 344 356 372 350" fill="none" ' +
  'stroke="#EDEFF3" stroke-width="56" stroke-linecap="round"/>' +
  '<path d="M272 212 L392 212" fill="none" stroke="#EDEFF3" stroke-width="56" ' +
  'stroke-linecap="round"/>';
const CUT = '<mask id="cut"><rect width="512" height="512" fill="#fff"/>' +
  '<circle cx="415" cy="97" r="119" fill="#000"/></mask>';

function icon(dot: string): string {
  return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">' +
    `${CUT}<g mask="url(#cut)">${LOGO}</g>${dot}</svg>`;
}

/** The dot lit: public/favicon.svg, the icon before the page runs. */
export const LIT = icon('<circle cx="415" cy="97" r="97" fill="#46B95C"/>');
const DIM = icon('<circle cx="415" cy="97" r="97" fill="#46B95C" opacity=".25"/>');
const AWAY = icon('<circle cx="415" cy="97" r="84" fill="none" stroke="#7D838E" ' +
  'stroke-width="26"/>');
/** Lit, then dim, each this long. */
const BLINK_MS = 600;

export type TabIcon = "lit" | "blinking" | "away";

function put(svg: string): void {
  const link = document.querySelector<HTMLLinkElement>("link[rel='icon']");
  const href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
  if (link && link.getAttribute("href") !== href) link.setAttribute("href", href);
}

/** The tab's icon as it should be now; returns what stops the blinking. */
export function tabIcon(state: TabIcon): () => void {
  if (state !== "blinking") {
    put(state === "away" ? AWAY : LIT);
    return () => {};
  }
  let lit = true;
  put(LIT);
  const timer = window.setInterval(() => {
    lit = !lit;
    put(lit ? LIT : DIM);
  }, BLINK_MS);
  return () => window.clearInterval(timer);
}
