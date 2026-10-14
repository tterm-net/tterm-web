// The tabs this browser keeps open: terminals from any project, in the order
// they were opened. A tab is only a view. Closing it leaves the terminal
// running in tTerm Web, in the sidebar under its machine, where it can be
// opened again.
//
// And the page's own shortcuts: Cmd on a Mac, Ctrl+Shift elsewhere. Plain
// Ctrl stays the terminal's: Ctrl+C, Ctrl+R and Ctrl+K belong to the shell.

const TABS_KEY = "tterm.tabs";
const ID = /^[0-9a-f-]{36}$/;
/** Tabs at once, at most: each is a terminal on the page's one connection,
 * which takes 64. */
export const TABS_MAX = 32;

export function rememberedTabs(): string[] {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(TABS_KEY) ?? "[]");
    if (!Array.isArray(raw)) return [];
    const ids = raw.filter((x): x is string => typeof x === "string" && ID.test(x));
    return [...new Set(ids)].slice(0, TABS_MAX);
  } catch {
    return [];
  }
}

export function rememberTabs(ids: string[]): void {
  try {
    localStorage.setItem(TABS_KEY, JSON.stringify(ids));
  } catch {
    // A convenience: without it the tabs are the running terminals.
  }
}

export const MAC = /Mac|iPhone|iPad/.test(navigator.platform ||
  navigator.userAgent);

/** Whether the key is our shortcut with this letter, in any keyboard
 * layout: with a Russian one the key K types Л. */
export function shortcut(event: KeyboardEvent, letter: string): boolean {
  const key = event.key.toLowerCase() === letter ||
    event.code === `Key${letter.toUpperCase()}`;
  if (event.altKey || !key) return false;
  return MAC ? event.metaKey && !event.ctrlKey && !event.shiftKey
    : event.ctrlKey && event.shiftKey && !event.metaKey;
}

export const JUMP_KEYS = MAC ? "⌘K" : "Ctrl+Shift+K";
