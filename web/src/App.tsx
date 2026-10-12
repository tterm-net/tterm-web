import { useCallback, useEffect, useRef, useState } from "react";
import { AddMachine } from "./AddMachine";
import {
  loadState, openTerminal, saveTheme, type MachineKind, type State,
  type Theme,
} from "./api";
import { ChevronRight } from "./icons";
import { Machines } from "./Machines";
import { Sidebar } from "./Sidebar";
import { TerminalView } from "./TerminalView";
import {
  applyTheme, onSystemThemeChange, rememberedPin, rememberPin, shown,
  type Shown,
} from "./theme";
import { Welcome } from "./Welcome";

/** Hovering the ⟩ opens the sidebar after this pause, so that a mouse merely
 * passing by does not throw it open. */
const OPEN_DELAY_MS = 180;
/** Leaving the sidebar closes it after this pause, forgiving a sloppy mouse. */
const CLOSE_DELAY_MS = 250;
const TOAST_MS = 3200;

/** /t/<id> is a terminal; anything else is the machines page. */
const TERMINAL_PATH = /^\/t\/([0-9a-f-]{36})\/?$/;

/** A first guess at the window in characters, for the shell to start with;
 * the terminal measures itself exactly as soon as it is on screen. */
function guessSize(): { cols: number; rows: number } {
  return {
    cols: Math.max(20, Math.floor((window.innerWidth - 32) / 7.8)),
    rows: Math.max(5, Math.floor((window.innerHeight - 64) / 15)),
  };
}

export function App() {
  const [state, setState] = useState<State | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>("system");
  const [shownTheme, setShownTheme] = useState<Shown>(() =>
    document.documentElement.classList.contains("tt-light") ? "light" : "dark");
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(rememberedPin);
  const [toast, setToast] = useState<string | null>(null);
  const [adding, setAdding] =
    useState<{ projectId: string; kind: MachineKind } | null>(null);
  const [path, setPath] = useState(location.pathname);
  const openTimer = useRef<number | undefined>(undefined);
  const closeTimer = useRef<number | undefined>(undefined);
  const toastTimer = useRef<number | undefined>(undefined);

  const load = useCallback(() => {
    setProblem(null);
    loadState()
      .then((loaded) => {
        setState(loaded);
        setTheme(loaded.me.theme);
      })
      .catch(() =>
        setProblem("tTerm Web is not answering. Is python -m tterm.dev " +
          "running in a terminal window?"));
  }, []);

  useEffect(load, [load]);

  /** After a machine connects or a terminal opens or ends: the sidebar and
   * the page learn of it. */
  const refresh = useCallback(() => {
    loadState().then(setState).catch(() => {});
  }, []);

  // Addresses: back and forward move between the machines page and
  // terminals the way they would between pages.
  const navigate = useCallback((to: string) => {
    if (to !== location.pathname) history.pushState(null, "", to);
    setPath(to);
  }, []);
  useEffect(() => {
    const onPop = () => setPath(location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // The theme: apply the choice, and follow the system while it says so.
  useEffect(() => {
    if (!state) return;
    applyTheme(theme);
    setShownTheme(shown(theme));
    if (theme !== "system") return;
    return onSystemThemeChange(() => {
      applyTheme("system");
      setShownTheme(shown("system"));
    });
  }, [theme, state]);

  const notYet = useCallback((what: string) => {
    setToast(what);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), TOAST_MS);
  }, []);

  const startTerminal = useCallback((machineId: string) => {
    const { cols, rows } = guessSize();
    openTerminal(machineId, cols, rows)
      .then((terminal) => {
        navigate(`/t/${terminal.id}`);
        refresh();
      })
      .catch((e: Error) => notYet(`Could not open a terminal: ${e.message}`));
  }, [navigate, refresh, notYet]);

  const toggleTheme = () => {
    const next: Theme = shown(theme) === "light" ? "dark" : "light";
    setTheme(next);
    saveTheme(next).catch(() =>
      notYet("Could not save the theme: tTerm Web did not answer."));
  };

  // The sidebar.
  const cancelTimers = () => {
    window.clearTimeout(openTimer.current);
    window.clearTimeout(closeTimer.current);
  };
  const hoverOpen = () => {
    cancelTimers();
    openTimer.current = window.setTimeout(() => setOpen(true), OPEN_DELAY_MS);
  };
  const hoverAway = () => {
    cancelTimers();
    if (!pinned) {
      closeTimer.current = window.setTimeout(() => setOpen(false),
        CLOSE_DELAY_MS);
    }
  };
  const togglePin = () => {
    const next = !pinned;
    setPinned(next);
    rememberPin(next);
    setOpen(true);
  };
  const closeSidebar = () => {
    cancelTimers();
    setOpen(false);
    setPinned(false);
    rememberPin(false);
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pinned && !adding) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pinned, adding]);

  useEffect(() => () => {
    cancelTimers();
    window.clearTimeout(toastTimer.current);
  }, []);

  if (problem) {
    return (
      <div className="status-page">
        <div className="status-card" role="alert">
          <h1>No connection</h1>
          <p>{problem}</p>
          <button type="button" className="button-accent" onClick={load}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  if (!state) {
    return <div className="status-page" aria-busy="true" />;
  }

  const sidebarShown = open || pinned;
  const personal = state.projects.find((p) => p.role === "owner");
  const anyMachine = state.projects.some((p) => p.machines.length > 0);
  const startAdding = (projectId: string, kind: MachineKind = "server") => {
    if (!pinned) setOpen(false);
    setAdding({ projectId, kind });
  };
  const leave = () => {
    if (!pinned) setOpen(false);
    navigate("/");
  };
  const terminalId = TERMINAL_PATH.exec(path)?.[1];

  const sidebarButton = !pinned && (
    <button type="button" className="icon-button"
      aria-label="Open sidebar" aria-expanded={sidebarShown}
      onClick={() => (sidebarShown ? closeSidebar() : setOpen(true))}
      onMouseEnter={hoverOpen}
      onMouseLeave={() => window.clearTimeout(openTimer.current)}>
      <ChevronRight />
    </button>
  );

  return (
    <div className={pinned ? "app pinned" : "app"}>
      {terminalId ? (
        <TerminalView key={terminalId} terminalId={terminalId}
          shownTheme={shownTheme} sidebarButton={sidebarButton}
          onLeave={leave} onOpenNew={startTerminal} onChanged={refresh} />
      ) : (
        <>
          <header className="topbar">{sidebarButton}</header>
          {anyMachine ? (
            <Machines projects={state.projects} terminals={state.terminals}
              onAdd={(projectId) => startAdding(projectId)}
              onOpenTerminal={startTerminal}
              onGoTo={(id) => navigate(`/t/${id}`)} />
          ) : (
            <Welcome me={state.me} personal={personal}
              onAdd={(kind) => personal && startAdding(personal.id, kind)} />
          )}
        </>
      )}

      {sidebarShown && (
        <Sidebar
          me={state.me}
          projects={state.projects}
          pinned={pinned}
          shownTheme={shownTheme}
          onToggleTheme={toggleTheme}
          onTogglePin={togglePin}
          onClose={closeSidebar}
          onMouseEnter={cancelTimers}
          onMouseLeave={hoverAway}
          onAddMachine={(projectId) => startAdding(projectId)}
          terminals={state.terminals}
          currentTerminal={terminalId}
          onOpenTerminal={(machineId) => {
            if (!pinned) setOpen(false);
            startTerminal(machineId);
          }}
          onGoTo={(id) => {
            if (!pinned) setOpen(false);
            navigate(`/t/${id}`);
          }}
          notYet={notYet}
        />
      )}

      {adding && (
        <AddMachine projects={state.projects} projectId={adding.projectId}
          kind={adding.kind} onClose={() => setAdding(null)}
          onConnected={refresh} notYet={notYet}
          onOpenTerminal={(machineId) => {
            setAdding(null);
            startTerminal(machineId);
          }} />
      )}

      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
