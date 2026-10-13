// A terminal on the page: its tab, and under it either the feed of blocks
// with tTerm's own input line (the line mode, from 0.0.5) or the classic
// terminal of 0.0.4, where every key goes straight to the shell. The switch
// is under the input line; the choice is kept per browser.
//
// The terminal lives in the core. Closing this view, reloading the page or
// losing the connection leaves it running; coming back attaches again.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { terminalInfo, type TerminalInfo } from "./api";
import { shortPath } from "./BlockView";
import { ClassicTerminal, type ViewEvents } from "./ClassicTerminal";
import { FeedTerminal } from "./FeedTerminal";
import { Close } from "./icons";
import { ModeToggle } from "./InputLine";
import { socket } from "./socket";
import type { Shown } from "./theme";

type Mode = "line" | "classic";
const MODE_KEY = "tterm.mode";

function rememberedMode(): Mode {
  try {
    return localStorage.getItem(MODE_KEY) === "classic" ? "classic" : "line";
  } catch {
    return "line";
  }
}

function rememberMode(mode: Mode): void {
  try {
    localStorage.setItem(MODE_KEY, mode);
  } catch {
    // A convenience; the line mode is the default anyway.
  }
}

interface Props {
  terminalId: string;
  shownTheme: Shown;
  sidebarButton: ReactNode;
  onLeave: () => void;
  onOpenNew: (machineId: string) => void;
  onChanged: () => void;
  notify: (text: string) => void;
}

export function TerminalView(props: Props) {
  const { terminalId, shownTheme, onChanged } = props;
  const [info, setInfo] = useState<TerminalInfo | null>(null);
  const [missing, setMissing] = useState(false);
  const [running, setRunning] = useState(false);
  const [online, setOnline] = useState(true);
  const [mode, setMode] = useState<Mode>(rememberedMode);
  const changed = useRef(onChanged);
  changed.current = onChanged;

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

  const label = info?.label;
  useEffect(() => {
    if (label) document.title = `${label} · tTerm`;
    return () => {
      document.title = "tTerm";
    };
  }, [label]);

  const events: ViewEvents = {
    attached: (terminal) => {
      setInfo(terminal);
      setMissing(false);
    },
    marker: (event) => {
      setRunning(event.t === "C");
      if (event.t === "P") {
        setInfo((now) => now && { ...now, cwd: event.cwd, home: event.home,
          branch: event.branch });
      }
    },
    state: (terminal) => {
      setInfo(terminal);
      if (terminal.state === "ended") {
        setRunning(false);
        changed.current();
      }
    },
    gone: (terminal) => {
      setRunning(false);
      if (terminal) setInfo(terminal);
      else setMissing(true);
      changed.current();
    },
  };

  const state = missing ? "gone" : info?.state ?? "connecting";
  const isEnded = state === "ended" || state === "gone";
  const dot = isEnded ? "dot ended" : state === "connecting" || running
    ? "dot busy" : "dot open";
  const where = info?.cwd ? shortPath(info.cwd, info.home) : "";
  // A shell that does not mark where commands start has no blocks.
  const noBlocks = info?.state === "open" && info.blocks === false
    ? "This shell does not mark where commands start, so it runs in the " +
      "classic mode only." : undefined;
  const shownMode: Mode = noBlocks ? "classic" : mode;
  const choose = (next: Mode) => {
    if (next === mode) return;
    setMode(next);
    rememberMode(next);
  };

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

      <div className={`terminal-area ${shownMode === "line" ? "feed-mode"
        : "classic-mode"}${isEnded ? " ended" : ""}`}>
        {shownMode === "classic" ? (
          <>
            <div className="classic-wrap">
              <ClassicTerminal key={`c-${terminalId}`} terminalId={terminalId}
                shownTheme={shownTheme} ended={isEnded} events={events} />
            </div>
            {!isEnded && (
              <footer className="classic-footer">
                <div className="input-hints">
                  <span>Classic: every key goes straight to the shell, with its
                    own Tab and history.</span>
                  <span className="amber">No confirmation for dangerous
                    commands in this mode.</span>
                </div>
                <ModeToggle mode="classic" disabled={noBlocks}
                  onLine={() => choose("line")} onClassic={() => {}} />
              </footer>
            )}
          </>
        ) : (
          <FeedTerminal key={`f-${terminalId}`} terminalId={terminalId}
            shownTheme={shownTheme} ended={isEnded} events={events}
            machine={info?.machine ?? "the machine"} home={info?.home ?? ""}
            onClassic={() => choose("classic")} notify={props.notify} />
        )}
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
