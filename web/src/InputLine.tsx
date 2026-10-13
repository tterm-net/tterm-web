// tTerm's own input line, under the feed. The line stays the page's until
// Enter: several lines, the mouse, a grey suggestion from history, Tab that
// asks the machine, history from every device. Enter hands the whole command
// to the shell at its prompt.
//
// Above it, the directory and the branch are buttons, as in Warp: one lists
// the directories to cd into, the other the branches to switch to. Either
// runs an ordinary command, so it shows up in the feed like any other.

import {
  useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
  type MutableRefObject,
} from "react";
import {
  completeWord, listBranches, listDirs, type CompleteItem, type Completion,
} from "./api";
import { shortPath } from "./BlockView";
import { ArrowUp, Branch, Folder } from "./icons";
import { typeInto, type Home } from "./keys";
import { Picker, type PickerItem } from "./Picker";

export type Phase = "start" | "prompt" | "running" | "input" | "full";

export interface Prompt {
  cwd: string;
  home: string;
  branch: string;
  venv: string;
}

interface Props {
  terminalId: string;
  phase: Phase;
  prompt: Prompt;
  /** Commands, newest first. */
  history: string[];
  /** A command to put back into the line (one the shell refused). */
  restore: { text: string; n: number } | null;
  homeRef: MutableRefObject<Home | null>;
  onRun: (command: string) => void;
  /** Ctrl+C for the shell. */
  onInterrupt: () => void;
  onClear: () => void;
  onClassic: () => void;
  notify: (text: string) => void;
}

const SPECIAL = /[\s'"\\$`!*?;&|<>()[\]{}#~]/;

function shQuote(value: string): string {
  return "'" + value.replace(/'/g, "'\\''") + "'";
}

/** cd into a directory, written the plainest way that is still safe. */
export function cdCommand(target: string): string {
  if (target === "..") return "cd ..";
  if (target === "~") return "cd ~";
  if (target.startsWith("~/")) {
    const rest = target.slice(2);
    return !rest || !SPECIAL.test(rest) ? `cd ${target}` : `cd ~/${shQuote(rest)}`;
  }
  if (target.startsWith("-")) return `cd ${shQuote("./" + target)}`;
  return SPECIAL.test(target) ? `cd ${shQuote(target)}` : `cd ${target}`;
}

export function switchCommand(branch: string): string {
  return SPECIAL.test(branch) ? `git switch ${shQuote(branch)}`
    : `git switch ${branch}`;
}

function commonPrefix(texts: string[]): string {
  if (!texts.length) return "";
  let prefix = texts[0];
  for (const t of texts.slice(1)) {
    let i = 0;
    while (i < prefix.length && i < t.length && prefix[i] === t[i]) i++;
    prefix = prefix.slice(0, i);
  }
  return prefix;
}

interface Listing {
  completion: Completion;
  active: number;
}

type Open = "dirs" | "branches" | "history" | null;

export function InputLine(props: Props) {
  const { phase, prompt, history, terminalId } = props;
  const [text, setText] = useState("");
  const [atEnd, setAtEnd] = useState(true);
  const [listing, setListing] = useState<Listing | null>(null);
  const [open, setOpen] = useState<Open>(null);
  const [items, setItems] = useState<PickerItem[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const caret = useRef<number | null>(null);
  const nav = useRef<{ index: number; prefix: string; draft: string } | null>(null);
  const asking = useRef(0);
  const ready = phase === "prompt";
  /** The line takes typing while a command runs too: the next command can
   * be typed ahead, and runs once the prompt is back. */
  const usable = phase !== "start";

  // Put the caret where an edit left it, once React has drawn the text.
  useLayoutEffect(() => {
    const el = field.current;
    if (!el) return;
    if (caret.current !== null) {
      el.setSelectionRange(caret.current, caret.current);
      caret.current = null;
    }
    // The field grows with its lines, up to a point (CSS max-height).
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
    setAtEnd(el.selectionStart === el.value.length);
  }, [text]);

  useEffect(() => {
    if (props.restore) {
      setText(props.restore.text);
      caret.current = props.restore.text.length;
    }
  }, [props.restore]);

  const ghost = useMemo(() => {
    if (!usable || !text || text.includes("\n") || !atEnd || listing) return "";
    const hit = history.find((h) => h.length > text.length &&
      h.startsWith(text) && !h.includes("\n"));
    return hit ? hit.slice(text.length) : "";
  }, [usable, text, atEnd, listing, history]);

  const set = (value: string, at = value.length) => {
    setText(value);
    caret.current = at;
  };

  const submit = useCallback(() => {
    const el = field.current;
    const command = el ? el.value : "";
    if (!command.trim()) return;
    if (!ready) {
      props.notify("A command is still running. Wait for it, or stop it " +
        "with Ctrl+C in its block.");
      return;
    }
    props.onRun(command);
    setText("");
    nav.current = null;
    setListing(null);
  }, [props, ready]);

  // The line, for the keys that belong to it.
  useEffect(() => {
    const el = field.current;
    props.homeRef.current = el && usable ? {
      element: el,
      type: (data) => (data === "\r" ? submit() : typeInto(el, data)),
    } : null;
  });

  const historyStep = (older: boolean): boolean => {
    let n = nav.current;
    if (!n) {
      if (!older) return false;
      n = { index: -1, prefix: text.includes("\n") ? "" : text, draft: text };
    }
    const step = older ? 1 : -1;
    for (let i = n.index + step; i >= 0 && i < history.length; i += step) {
      if (history[i].startsWith(n.prefix) && history[i] !== text) {
        nav.current = { ...n, index: i };
        set(history[i]);
        return true;
      }
    }
    if (!older) {
      nav.current = null;
      set(n.draft);
      return true;
    }
    return false;
  };

  const applyItem = (item: CompleteItem, c: Completion, single: boolean) => {
    const value = field.current?.value ?? text;
    const before = value.slice(0, c.start);
    const after = value.slice(c.end);
    let insert = item.text;
    if (single && item.kind !== "dir") {
      insert += c.quote;
      if (!after.startsWith(" ")) insert += " ";
    }
    set(before + insert + after, before.length + insert.length);
  };

  const askTab = async () => {
    const el = field.current;
    if (!el) return;
    const value = el.value;
    const at = el.selectionStart ?? value.length;
    const n = ++asking.current;
    let c: Completion;
    try {
      // The core counts characters; the page, UTF-16 units. An emoji is
      // one of the first and two of the second.
      c = await completeWord(terminalId, value, Array.from(value.slice(0, at)).length);
    } catch {
      return;
    }
    // Typed on since: this answer is about a line that is gone.
    if (n !== asking.current || field.current?.value !== value) return;
    const units = (chars: number) => Array.from(value).slice(0, chars).join("").length;
    c = { ...c, start: units(c.start), end: units(c.end) };
    if (c.items.length === 0) return;
    if (c.items.length === 1) {
      applyItem(c.items[0], c, true);
      return;
    }
    const common = commonPrefix(c.items.map((i) => i.text));
    const word = value.slice(c.start, c.end);
    if (common.length > word.length) {
      set(value.slice(0, c.start) + common + value.slice(c.end),
        c.start + common.length);
      c = { ...c, end: c.start + common.length };
    }
    setListing({ completion: c, active: -1 });
  };

  const pickListed = (index: number) => {
    if (!listing) return;
    const item = listing.completion.items[index];
    if (item) applyItem(item, listing.completion, true);
    setListing(null);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing) return;
    const el = event.currentTarget;
    if (listing) {
      const count = listing.completion.items.length;
      if (event.key === "Tab" || event.key === "ArrowDown" ||
          event.key === "ArrowUp") {
        event.preventDefault();
        const back = event.key === "ArrowUp" || (event.key === "Tab" && event.shiftKey);
        setListing({ ...listing,
          active: (listing.active + (back ? -1 : 1) + count) % count });
        return;
      }
      if (event.key === "Enter" && listing.active >= 0) {
        event.preventDefault();
        pickListed(listing.active);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setListing(null);
        return;
      }
      setListing(null);
    }
    if (event.key === "Enter" && !event.shiftKey && !event.altKey) {
      event.preventDefault();
      submit();
      return;
    }
    if (event.key === "Tab" && !event.shiftKey) {
      event.preventDefault();
      if (!usable) return;
      if (ghost) {
        set(text + ghost);
        return;
      }
      void askTab();
      return;
    }
    if ((event.key === "ArrowRight" || event.key === "End") && ghost &&
        el.selectionStart === el.value.length) {
      event.preventDefault();
      set(text + ghost);
      return;
    }
    if (event.key === "ArrowUp" && !event.shiftKey &&
        !el.value.slice(0, el.selectionStart ?? 0).includes("\n")) {
      if (historyStep(true)) event.preventDefault();
      return;
    }
    if (event.key === "ArrowDown" && !event.shiftKey && nav.current &&
        !el.value.slice(el.selectionEnd ?? 0).includes("\n")) {
      if (historyStep(false)) event.preventDefault();
      return;
    }
    if (event.ctrlKey && !event.metaKey && !event.altKey) {
      const k = event.key.toLowerCase();
      if (k === "c" && el.selectionStart === el.selectionEnd) {
        event.preventDefault();
        // On an empty line Ctrl+C is meant for the shell: it stops what
        // runs, and at the prompt it sets back whatever it was waiting for.
        if (!el.value && usable) props.onInterrupt();
        set("");
        nav.current = null;
      } else if (k === "l") {
        event.preventDefault();
        props.onClear();
      } else if (k === "r") {
        event.preventDefault();
        openPicker("history");
      }
    }
  };

  const openPicker = (which: Exclude<Open, null>) => {
    setListing(null);
    setProblem(null);
    setOpen(which);
    if (which === "history") {
      setItems(history.map((h, i) => ({ key: String(i), label: h })));
      return;
    }
    setItems(null);
    const request = which === "dirs"
      ? listDirs(terminalId).then((d): PickerItem[] => [
        ...(d.cwd !== "/" ? [{ key: "..", label: "..", hint: "up a level",
          icon: <ArrowUp size={14} /> }] : []),
        ...d.dirs.map((name) => ({ key: name, label: name,
          icon: <Folder size={14} />, hidden: name.startsWith(".") })),
      ])
      : listBranches(terminalId).then((b): PickerItem[] => {
        const local = new Set(b.local);
        return [
          ...b.local.map((name) => ({ key: `l:${name}`, label: name,
            hint: name === b.current ? "current" : undefined,
            icon: <Branch size={14} /> })),
          ...b.remote
            .filter((r) => !local.has(r.slice(r.indexOf("/") + 1)))
            .map((name) => ({ key: `r:${name}`, label: name, hint: "remote",
              icon: <Branch size={14} /> })),
        ];
      });
    request.then(setItems).catch((e: Error) =>
      setProblem(`The machine did not answer: ${e.message}`));
  };

  const close = () => {
    setOpen(null);
    window.setTimeout(() => field.current?.focus(), 0);
  };

  const picked = (item: PickerItem) => {
    const which = open;
    close();
    if (which === "history") {
      set(item.label);
      return;
    }
    if (which === "dirs") {
      props.onRun(cdCommand(item.label));
    } else if (which === "branches") {
      if (item.hint === "current") return;
      const name = item.key.startsWith("r:")
        ? item.label.slice(item.label.indexOf("/") + 1) : item.label;
      props.onRun(switchCommand(name));
    }
  };

  const where = shortPath(prompt.cwd, prompt.home) || "…";
  const placeholder = phase === "start" ? "Starting the shell…"
    : phase === "running" || phase === "input" || phase === "full"
      ? "Keys go to the running command. Click here to type the next one."
      : "";

  return (
    <footer className="input-footer">
      <div className="input-row">
        {open && (
          <Picker
            label={open === "dirs" ? "Change directory"
              : open === "branches" ? "Switch branch" : "History"}
            items={items} error={problem}
            placeholder={open === "dirs" ? "Find a directory, or type a path"
              : open === "branches" ? "Find a branch" : "Find a command"}
            onPick={picked} onClose={close}
            onTyped={open === "dirs" ? (typed) => {
              close();
              props.onRun(cdCommand(typed));
            } : undefined}
            typedLabel={(typed) => cdCommand(typed)} />
        )}
        <div className="prompt-chips">
          <button type="button" className="chip chip-dir" disabled={!ready}
            title="Change directory" onClick={() => openPicker("dirs")}>
            {where}
          </button>
          {prompt.branch && (
            <button type="button" className="chip chip-branch" disabled={!ready}
              title="Switch branch" onClick={() => openPicker("branches")}>
              <Branch size={12} />
              {prompt.branch}
            </button>
          )}
          {prompt.venv && <span className="chip-venv">({prompt.venv})</span>}
          <span className="prompt-arrow" aria-hidden="true">❯</span>
        </div>
        <div className="input-box">
          {listing && (
            <ul className="complete-list" role="listbox" aria-label="Completions">
              {listing.completion.items.map((item, i) => (
                <li key={item.text} role="option"
                  aria-selected={i === listing.active}
                  className={`kind-${item.kind}`}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    pickListed(i);
                  }}>
                  {item.label}
                </li>
              ))}
              {listing.completion.more && (
                <li className="complete-more" aria-disabled="true">
                  More not shown: type a few more letters
                </li>
              )}
            </ul>
          )}
          <div className="input-ghost" aria-hidden="true">
            <span className="ghost-typed">{text}</span>
            <span className="ghost-rest">{ghost}</span>
          </div>
          <textarea ref={field} className="input-field" rows={1} value={text}
            aria-label="Command" spellCheck={false} autoComplete="off"
            autoCapitalize="off" autoCorrect="off" placeholder={placeholder}
            disabled={!usable}
            onChange={(e) => {
              setText(e.target.value);
              nav.current = null;
            }}
            onSelect={(e) => setAtEnd(e.currentTarget.selectionStart ===
              e.currentTarget.value.length)}
            onKeyDown={onKeyDown} />
        </div>
        <ModeToggle mode="line" onLine={() => {}} onClassic={props.onClassic} />
      </div>
      <div className="input-hints">
        <span><kbd>Tab</kbd> complete, or take the suggestion</span>
        <span><kbd>↑</kbd> history across your devices</span>
        <span><kbd>⇧↵</kbd> new line</span>
        <span>While a command runs, what you type goes to it</span>
      </div>
    </footer>
  );
}

export function ModeToggle({ mode, onLine, onClassic, disabled }: {
  mode: "line" | "classic";
  onLine: () => void;
  onClassic: () => void;
  disabled?: string;
}) {
  return (
    <div role="group" aria-label="Input mode" className="mode-toggle"
      title={disabled}>
      <button type="button" aria-pressed={mode === "line"} disabled={!!disabled}
        onClick={onLine}>Line</button>
      <button type="button" aria-pressed={mode === "classic"} onClick={onClassic}>
        Classic
      </button>
    </div>
  );
}
