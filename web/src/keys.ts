// Keeps the keyboard where typing belongs: tTerm's input line at the prompt,
// the terminal while a command runs, in a full-screen program and in the
// classic mode. Without it a click on a button or the bar takes the focus
// away, the cursor stops blinking and keys go nowhere.
//
// A click on anything that takes no typing hands the keys back right after
// it did its job, and so does coming back to this window. A key pressed
// while the focus is elsewhere is not lost: it goes where it belongs. Fields,
// open dialogs, the account's menu and the browser's own shortcuts are left
// alone.

export interface Home {
  /** The element that should hold the keys. */
  element: HTMLElement | null;
  /** Puts in a key that was typed while the focus was elsewhere. */
  type: (data: string) => void;
}

/** `home` says where keys go now, or null when nowhere (an ended terminal).
 * Returns what undoes it. */
export function keepKeys(home: () => Home | null): () => void {
  const typesHere = (el: Element | null) =>
    !!el?.closest("input, textarea, select, [contenteditable='true']");
  const pressable = (el: Element | null) =>
    !!el?.closest("button, a[href], summary, [role='button']");
  // A picker left open in a tab behind does not count. The account's menu
  // (0.1.4) keeps the keys as well: it opens in the sidebar, over a live
  // terminal. Other menus are on pages, which may stay open behind it.
  const dialogOpen = () => document.querySelector(
    ".dialog, .finder, .sidebar [role='menu'], .terminal-view:not(.behind) .picker") !== null;
  const target = (): Home | null => {
    const h = home();
    if (!h?.element) return null;
    const active = document.activeElement;
    if (active === h.element || typesHere(active) || dialogOpen()) return null;
    return h;
  };
  const reclaim = () => target()?.element?.focus();
  // A click that ended a selection leaves it be: focusing a field would
  // wipe the selected text before it could be copied.
  const selecting = () => {
    const selection = window.getSelection();
    return !!selection && !selection.isCollapsed &&
      selection.toString().length > 0;
  };
  const onClick = () => window.setTimeout(() => {
    if (!selecting()) reclaim();
  }, 0);
  const onKey = (event: KeyboardEvent) => {
    if (event.defaultPrevented) return;
    const h = target();
    if (!h) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === "Escape" || event.key === "Tab") return;
    if ((event.key === "Enter" || event.key === " ") &&
        pressable(document.activeElement)) {
      return;
    }
    h.element?.focus();
    const data = event.key === "Enter" ? "\r"
      : event.key === "Backspace" ? "\x7f"
      : event.key.length === 1 ? event.key : "";
    if (data) {
      event.preventDefault();
      h.type(data);
    }
  };
  const onFocus = () => {
    if (!selecting()) reclaim();
  };
  document.addEventListener("click", onClick, true);
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("focus", onFocus);
  return () => {
    document.removeEventListener("click", onClick, true);
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("focus", onFocus);
  };
}

/** A field outside `own` has the keys: someone is typing there, a name in
 * the sidebar say (0.1.2), and a terminal coming up does not take them. */
export function typingElsewhere(own: Element | null | undefined): boolean {
  const active = document.activeElement;
  return !!active?.closest("input, textarea, select, [contenteditable='true']") &&
    !own?.contains(active);
}

/** Types into a text field at its caret, so that React hears of it. */
export function typeInto(field: HTMLTextAreaElement | HTMLInputElement,
  data: string): void {
  if (data === "\x7f") {
    const start = field.selectionStart ?? field.value.length;
    const end = field.selectionEnd ?? start;
    if (start === end && start > 0) {
      field.setRangeText("", start - 1, end, "end");
    } else {
      field.setRangeText("", start, end, "end");
    }
  } else if (data !== "\r") {
    field.setRangeText(data, field.selectionStart ?? field.value.length,
      field.selectionEnd ?? field.value.length, "end");
  }
  field.dispatchEvent(new Event("input", { bubbles: true }));
}
