// The classic mode: one terminal over the whole area, every key straight to
// the shell, and the shell draws everything, its prompt included. Its own Tab
// and history work, and nothing is checked before a command runs.
//
// The terminal lives in the core. Closing this view, reloading the page or
// losing the connection leaves it running; coming back attaches again and
// the screen is drawn as it was.

import { FitAddon } from "@xterm/addon-fit";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebglAddon } from "@xterm/addon-webgl";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef } from "react";
import type { TerminalInfo } from "./api";
import { keepKeys } from "./keys";
import { socket, type Channel, type MarkerEvent } from "./socket";
import type { Shown } from "./theme";
import { terminalTheme } from "./xtermTheme";

export const FONT = '"JetBrains Mono Variable", "JetBrains Mono", ' +
  'ui-monospace, SFMono-Regular, Menlo, monospace';
export const FONT_SIZE = 13;
/** Output handed to the terminal but not drawn yet. Above HIGH the core is
 * asked to wait; below LOW it may go on. Kept small, so that after Ctrl+C
 * on a flood the screen is there in a moment, not after megabytes. */
export const HIGH = 512 * 1024;
export const LOW = 128 * 1024;

export interface ViewEvents {
  attached: (terminal: TerminalInfo) => void;
  marker: (event: MarkerEvent) => void;
  state: (terminal: TerminalInfo) => void;
  gone: (terminal: TerminalInfo | null) => void;
}

interface Props {
  terminalId: string;
  shownTheme: Shown;
  /** An ended terminal takes no keys, so it does not take them back. */
  ended: boolean;
  /** Its tab is in front: only then does it take the keys. */
  active: boolean;
  events: ViewEvents;
}

export function ClassicTerminal(props: Props) {
  const { terminalId, shownTheme } = props;
  const box = useRef<HTMLDivElement>(null);
  const term = useRef<Terminal | null>(null);
  const channelRef = useRef<Channel | null>(null);
  const events = useRef(props.events);
  events.current = props.events;
  const ended = useRef(props.ended);
  ended.current = props.ended;
  const active = useRef(props.active);
  active.current = props.active;

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
      channel = channelRef.current = socket.attach(terminalId, t.cols, t.rows, {
        attached: (terminal, resumed) => {
          // Back after a moment away: what was missed is old news too, up
          // to `replayed`.
          if (resumed) replaying = true;
          events.current.attached(terminal);
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
        marker: (event) => events.current.marker(event),
        state: (terminal) => {
          events.current.state(terminal);
          if (terminal.state === "ended") stop();
        },
        gone: (terminal) => {
          stop();
          events.current.gone(terminal);
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
      cleanups.push(keepKeys(() => disposed || ended.current || !active.current
        ? null : {
        element: t.textarea ?? null,
        type: (data) => t.input(data),
      }));
      if (active.current) t.focus();
    };
    void start();

    return () => {
      disposed = true;
      for (const cleanup of cleanups) cleanup();
      cancelAnimationFrame(frame);
      observer?.disconnect();
      channel?.detach();
      channelRef.current = null;
      xterm?.dispose();
      term.current = null;
    };
  }, [terminalId]);

  // The theme follows the page's.
  useEffect(() => {
    if (term.current) term.current.options.theme = terminalTheme(shownTheme);
  }, [shownTheme]);

  // Its tab brought to the front: the keys come here. Sent behind: it no
  // longer claims them.
  useEffect(() => {
    if (!props.active) channelRef.current?.unfocus();
    if (props.active && !props.ended) term.current?.focus();
  }, [props.active, props.ended]);

  return (
    <div className="terminal-box classic-box" ref={box}
      onMouseDown={() => {
        if (!ended.current) term.current?.focus();
      }} />
  );
}
