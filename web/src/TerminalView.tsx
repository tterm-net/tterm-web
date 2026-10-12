// One terminal over the whole window, the classic way: every key goes
// straight to the shell, and the shell draws everything, its prompt
// included. The block feed and tTerm's own input line come in 0.0.5.
//
// The terminal lives in the core. Closing this view, reloading the page or
// losing the connection leaves it running; coming back attaches again and
// the screen is drawn as it was.

import { FitAddon } from "@xterm/addon-fit";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebglAddon } from "@xterm/addon-webgl";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { terminalInfo, type TerminalInfo } from "./api";
import { Close } from "./icons";
import { socket, type Channel } from "./socket";
import type { Shown } from "./theme";
import { terminalTheme } from "./xtermTheme";

const FONT = '"JetBrains Mono Variable", "JetBrains Mono", ui-monospace, ' +
  'SFMono-Regular, Menlo, monospace';
const FONT_SIZE = 13;
/** Output handed to the terminal but not drawn yet. Above HIGH the core is
 * asked to wait; below LOW it may go on. Kept small, so that after Ctrl+C
 * on a flood the screen is there in a moment, not after megabytes. */
const HIGH = 512 * 1024;
const LOW = 128 * 1024;

interface Props {
  terminalId: string;
  shownTheme: Shown;
  sidebarButton: ReactNode;
  onLeave: () => void;
  onOpenNew: (machineId: string) => void;
  onChanged: () => void;
}

export function TerminalView(props: Props) {
  const { terminalId, shownTheme, onChanged } = props;
  const box = useRef<HTMLDivElement>(null);
  const term = useRef<Terminal | null>(null);
  const [info, setInfo] = useState<TerminalInfo | null>(null);
  const [missing, setMissing] = useState(false);
  const [running, setRunning] = useState(false);
  const [online, setOnline] = useState(true);
  const changed = useRef(onChanged);
  changed.current = onChanged;
  /** An ended terminal takes no keys, so it does not take them back. */
  const ended = useRef(false);

  // What the terminal is, even before the socket says it.
  useEffect(() => {
    let live = true;
    setInfo(null);
    setMissing(false);
    terminalInfo(terminalId)
      .then((found) => {
        if (!live) return;
        if (found) setInfo((now) => now ?? found);
        else setMissing(true);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [terminalId]);

  useEffect(() => socket.onConnection(setOnline), []);

  // The terminal itself: made once per terminal id.
  useEffect(() => {
    const element = box.current;
    if (!element) return;
    let disposed = false;
    let channel: Channel | null = null;
    let observer: ResizeObserver | null = null;
    let xterm: Terminal | null = null;
    let pending = 0;
    let frame = 0;
    // While history is drawn again, the terminal answers questions programs
    // asked long ago (the cursor position, the colours); those answers must
    // not be typed into the shell now.
    let replaying = false;
    const cleanups: (() => void)[] = [];

    const start = async () => {
      // The font must be there before the terminal measures its cells.
      try {
        await document.fonts.load(`${FONT_SIZE}px "JetBrains Mono Variable"`);
      } catch {
        // A fallback font is measured instead; still a working terminal.
      }
      if (disposed) return;
      xterm = new Terminal({
        fontFamily: FONT,
        fontSize: FONT_SIZE,
        lineHeight: 1.15,
        cursorBlink: true,
        scrollback: 10000,
        allowProposedApi: true,
        theme: terminalTheme(
          document.documentElement.classList.contains("tt-light")
            ? "light" : "dark"),
      });
      term.current = xterm;
      const fit = new FitAddon();
      xterm.loadAddon(fit);
      xterm.loadAddon(new Unicode11Addon());
      xterm.unicode.activeVersion = "11";
      xterm.open(element);
      try {
        const webgl = new WebglAddon();
        webgl.onContextLoss(() => webgl.dispose());
        xterm.loadAddon(webgl);
      } catch {
        // No WebGL here: xterm.js draws with the DOM instead.
      }
      fit.fit();

      const t = xterm;
      // An ended terminal takes no keys and shows no cursor: nothing there
      // reads them any more. Its last screen stays, under the note.
      const stop = () => {
        t.options.disableStdin = true;
        t.options.cursorBlink = false;
        t.write("\x1b[?25l");
        t.blur();
      };
      channel = socket.attach(terminalId, t.cols, t.rows, {
        attached: (terminal) => {
          setInfo(terminal);
          setMissing(false);
        },
        reset: () => {
          t.reset();
          pending = 0;
          replaying = true;
        },
        replayed: () => {
          t.write(new Uint8Array(0), () => {
            replaying = false;
          });
        },
        output: (data) => {
          pending += data.length;
          if (pending > HIGH) channel?.pause();
          t.write(data, () => {
            pending -= data.length;
            if (pending < LOW) channel?.resume();
          });
        },
        marker: (event) => {
          setRunning(event.t === "C");
          if (event.t === "P") {
            setInfo((now) => now && { ...now, cwd: event.cwd,
              home: event.home, branch: event.branch });
          }
        },
        state: (terminal) => {
          setInfo(terminal);
          if (terminal.state === "ended") {
            setRunning(false);
            stop();
            changed.current();
          }
        },
        gone: (terminal) => {
          setRunning(false);
          stop();
          if (terminal) setInfo(terminal);
          else setMissing(true);
          changed.current();
        },
      });
      t.onData((data) => {
        if (!replaying) channel?.input(data);
      });
      t.onBinary((data) => {
        if (!replaying) channel?.inputBinary(data);
      });
      // The page clicked into last is the one that types (see Watcher in
      // tterm/core/terminals.py).
      t.textarea?.addEventListener("focus", () => channel?.focus());
      t.onResize(({ cols, rows }) => channel?.resize(cols, rows));
      observer = new ResizeObserver(() => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
          if (!disposed) fit.fit();
        });
      });
      observer.observe(element);
      cleanups.push(keepKeys(t, () => disposed || ended.current));
      t.focus();
    };
    void start();

    return () => {
      disposed = true;
      for (const cleanup of cleanups) cleanup();
      cancelAnimationFrame(frame);
      observer?.disconnect();
      channel?.detach();
      xterm?.dispose();
      term.current = null;
    };
  }, [terminalId]);

  // The theme follows the page's.
  useEffect(() => {
    if (term.current) term.current.options.theme = terminalTheme(shownTheme);
  }, [shownTheme]);

  const label = info?.label;
  useEffect(() => {
    if (label) document.title = `${label} · tTerm`;
    return () => {
      document.title = "tTerm";
    };
  }, [label]);

  const state = missing ? "gone" : info?.state ?? "connecting";
  const isEnded = state === "ended" || state === "gone";
  ended.current = isEnded;
  const dot = isEnded ? "dot ended" : state === "connecting" || running
    ? "dot busy" : "dot open";
  const where = info?.cwd ? shortPath(info.cwd, info.home) : "";

  return (
    <div className="terminal-view">
      <header className="topbar terminal-bar">
        {props.sidebarButton}
        <div className="tab" aria-current="page"
          title={info ? `${info.label}${where ? ` · ${where}` : ""}` : ""}>
          <span className={dot} aria-hidden="true" />
          <span className="tab-label">{info?.label ?? "Terminal"}</span>
          <button type="button" className="tab-close"
            aria-label="Close the tab"
            title="Close the tab. The terminal keeps running and stays in the sidebar under its machine."
            onClick={props.onLeave}>
            <Close size={13} />
          </button>
        </div>
      </header>

      {!online && (
        <div className="lost-strip" role="status">
          Lost the connection to tTerm Web. Reconnecting; the terminal keeps
          running.
        </div>
      )}

      <div className="terminal-area">
        <div className="terminal-box" ref={box}
          onMouseDown={() => term.current?.focus()} />
        {state === "connecting" && !missing && (
          <div className="terminal-note" role="status">
            <span className="spinner" aria-hidden="true" />
            Connecting to {info?.machine ?? "the machine"}…
          </div>
        )}
        {isEnded && (
          <div className="terminal-ended" role="status">
            <div className="ended-text">
              <strong>{missing && !info ? "No such terminal"
                : "This terminal has ended"}</strong>
              <span>{info?.reason ?? "It is no longer running."}</span>
            </div>
            <div className="button-row">
              {info && (
                <button type="button" className="button-accent big"
                  onClick={() => props.onOpenNew(info.machineId)}>
                  Open a new terminal on {info.machine}
                </button>
              )}
              <button type="button" className="button-plain"
                onClick={props.onLeave}>
                Back to machines
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Keeps the keyboard on the terminal. Without it xterm.js draws its cursor
 * hollow and still, and keys go nowhere. A click on anything that takes no
 * typing (a button, the bar, the sidebar) hands the keys back right after
 * it did its job, and so does coming back to this window. A key pressed
 * while the focus is elsewhere is not lost: it goes to the terminal. Fields,
 * open dialogs and the browser's own shortcuts are left alone. Returns what
 * undoes it.
 */
function keepKeys(t: Terminal, idle: () => boolean): () => void {
  const typesHere = (el: Element | null) =>
    !!el?.closest("input, textarea, select, [contenteditable='true']");
  const pressable = (el: Element | null) =>
    !!el?.closest("button, a[href], summary, [role='button']");
  const dialogOpen = () => document.querySelector(".dialog") !== null;
  const elsewhere = () => {
    const active = document.activeElement;
    return active !== t.textarea && !typesHere(active) && !dialogOpen()
      && !idle();
  };
  const reclaim = () => {
    if (elsewhere()) t.focus();
  };
  const onClick = () => window.setTimeout(reclaim, 0);
  const onKey = (event: KeyboardEvent) => {
    if (event.defaultPrevented || !elsewhere()) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === "Escape" || event.key === "Tab") return;
    const active = document.activeElement;
    if ((event.key === "Enter" || event.key === " ") && pressable(active)) {
      return;
    }
    t.focus();
    const data = event.key === "Enter" ? "\r"
      : event.key === "Backspace" ? "\x7f"
      : event.key.length === 1 ? event.key : "";
    if (data) {
      event.preventDefault();
      t.input(data);
    }
  };
  document.addEventListener("click", onClick, true);
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("focus", reclaim);
  return () => {
    document.removeEventListener("click", onClick, true);
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("focus", reclaim);
  };
}

/** /home/tterm/app as ~/app, the way prompts write it. */
function shortPath(cwd: string, home?: string): string {
  if (!home) return cwd;
  if (cwd === home) return "~";
  return cwd.startsWith(home + "/") ? "~" + cwd.slice(home.length) : cwd;
}
