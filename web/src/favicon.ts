// The tab's icon: tTerm's own, the arrow green instead of amber, so that a
// tab of the web is not taken for the site's (the owner, 25.10; the site and
// tTerm for the computer keep the amber arrow). 0.1.9 had a green dot in the
// corner; live it looked bad, and 0.1.10 colours the arrow instead, approved
// on the canvas, version 78: the arrow is the prompt, green is "connected".
//
// While a command runs in a terminal of this page the arrow blinks, as a
// cursor does, and while the page has lost tTerm Web it is grey. A tab does
// not play an animation inside its icon, so the page swaps the icon itself;
// a tab behind is slowed down by the browser, and blinks more slowly there.

const TILE = '<rect width="512" height="512" rx="116" fill="#131417"/>';
const T = '<path d="M330 140 L330 316 C330 344 344 356 372 350" fill="none" ' +
  'stroke="#EDEFF3" stroke-width="56" stroke-linecap="round"/>' +
  '<path d="M272 212 L392 212" fill="none" stroke="#EDEFF3" stroke-width="56" ' +
  'stroke-linecap="round"/>';

function icon(arrow: string): string {
  return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">' +
    `${TILE}<path d="M118 168 L206 256 L118 344" fill="none" ${arrow} ` +
    `stroke-width="56" stroke-linecap="round" stroke-linejoin="round"/>${T}</svg>`;
}

/** The arrow lit: public/favicon.svg, the icon before the page runs. */
export const LIT = icon('stroke="#46B95C"');
const DIM = icon('stroke="#46B95C" stroke-opacity=".25"');
const AWAY = icon('stroke="#7D838E"');
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
