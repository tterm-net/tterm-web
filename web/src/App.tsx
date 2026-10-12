import { useCallback, useEffect, useRef, useState } from "react";
import { loadState, saveTheme, type State, type Theme } from "./api";
import { ChevronRight } from "./icons";
import { Sidebar } from "./Sidebar";
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

export function App() {
  const [state, setState] = useState<State | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>("system");
  const [shownTheme, setShownTheme] = useState<Shown>(() =>
    document.documentElement.classList.contains("tt-light") ? "light" : "dark");
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(rememberedPin);
  const [toast, setToast] = useState<string | null>(null);
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
      if (event.key === "Escape" && !pinned) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pinned]);

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

  return (
    <div className={pinned ? "app pinned" : "app"}>
      <header className="topbar">
        {!pinned && (
          <button type="button" className="icon-button"
            aria-label="Open sidebar" aria-expanded={sidebarShown}
            onClick={() => (sidebarShown ? closeSidebar() : setOpen(true))}
            onMouseEnter={hoverOpen}
            onMouseLeave={() => window.clearTimeout(openTimer.current)}>
            <ChevronRight />
          </button>
        )}
      </header>

      <Welcome me={state.me} personal={personal} notYet={notYet} />

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
          notYet={notYet}
        />
      )}

      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
