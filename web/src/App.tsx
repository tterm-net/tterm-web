import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AddMachine } from "./AddMachine";
import {
  loadState, openTerminal, saveTheme, type MachineKind, type State,
  type TerminalInfo, type Theme,
} from "./api";
import { Finder, type FinderGroup, type FinderItem } from "./Finder";
import {
  ChevronRight, Close, Computer, Moon, Pin, Plus, Server, Sun,
} from "./icons";
import { Machines } from "./Machines";
import { Sidebar } from "./Sidebar";
import { socket } from "./socket";
import { TabBar, type Dot, type TabView } from "./TabBar";
import { rememberedTabs, rememberTabs, shortcut, TABS_MAX } from "./tabs";
import { TerminalView, type TabStatus } from "./TerminalView";
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
/** How often the page asks whether computers came online or went. */
const COMPUTERS_MS = 10_000;

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

function withTab(tabs: string[], id: string): string[] {
  if (tabs.includes(id)) return tabs;
  // Past the most there may be, the oldest tab goes; its terminal stays.
  return [...tabs, id].slice(-TABS_MAX);
}

/** A tab's dot: what the page has heard of it, or else what the list says. */
function dotOf(status: TabStatus | undefined, known: TerminalInfo | undefined,
  missing: boolean): Dot {
  const state = status?.state ?? known?.state ?? (missing ? "gone" : "connecting");
  if (state === "ended" || state === "gone") return "ended";
  if (status ? status.away : known?.away) return "away";
  if (state === "connecting" || status?.running) return "busy";
  return "open";
}

function sameStatus(a: TabStatus | undefined, b: TabStatus): boolean {
  return !!a && a.label === b.label && a.machine === b.machine &&
    a.where === b.where && a.state === b.state && a.away === b.away &&
    a.running === b.running;
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
  const [tabs, setTabs] = useState<string[]>(rememberedTabs);
  const [statuses, setStatuses] = useState<Record<string, TabStatus>>({});
  const [picking, setPicking] = useState(false);
  const [palette, setPalette] = useState(false);
  const [online, setOnline] = useState(true);
  const openTimer = useRef<number | undefined>(undefined);
  const closeTimer = useRef<number | undefined>(undefined);
  const toastTimer = useRef<number | undefined>(undefined);
  const pruned = useRef(false);

  const load = useCallback(() => {
    setProblem(null);
    loadState()
      .then((loaded) => {
        setState(loaded);
        setTheme(loaded.me.theme);
        if (!pruned.current) {
          // Tabs left from before: those of terminals still running stay.
          pruned.current = true;
          const live = new Set(loaded.terminals.map((t) => t.id));
          const here = TERMINAL_PATH.exec(location.pathname)?.[1];
          setTabs((ts) => ts.filter((id) => live.has(id) || id === here));
        }
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

  // A computer comes online when its agent starts and goes when it stops,
  // with nothing done on this page: while there are computers, ask now and
  // then, and whenever the window comes back into view.
  const computers = !!state?.projects.some((p) =>
    p.machines.some((m) => m.kind === "computer"));
  useEffect(() => {
    if (!computers) return;
    const timer = window.setInterval(refresh, COMPUTERS_MS);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [computers, refresh]);

  // Addresses: back and forward move between the machines page and
  // terminals the way they would between pages. In place, the address
  // takes the current one's step in the history instead of adding one.
  const navigate = useCallback((to: string, inPlace = false) => {
    if (to !== location.pathname) {
      if (inPlace) history.replaceState(null, "", to);
      else history.pushState(null, "", to);
    }
    setPath(to);
  }, []);
  useEffect(() => {
    const onPop = () => setPath(location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // A terminal opened by its address gets a tab; the tabs are remembered.
  const terminalId = TERMINAL_PATH.exec(path)?.[1] ?? null;
  useEffect(() => {
    if (terminalId) setTabs((ts) => withTab(ts, terminalId));
  }, [terminalId]);
  useEffect(() => rememberTabs(tabs), [tabs]);
  const shownTabs = terminalId ? withTab(tabs, terminalId) : tabs;

  const onStatus = useCallback((id: string, status: TabStatus) => {
    setStatuses((all) => (sameStatus(all[id], status) ? all
      : { ...all, [id]: status }));
  }, []);

  // The page's connection to tTerm Web.
  useEffect(() => socket.onConnection(setOnline), []);

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

  /** A new terminal on the machine, in a new tab. Opened from an ended
   * terminal (`instead`), it takes that one's tab and its address: Back
   * does not bring the ended one back. */
  const startTerminal = useCallback((machineId: string, instead?: string) => {
    const { cols, rows } = guessSize();
    openTerminal(machineId, cols, rows)
      .then((terminal) => {
        setStatuses((all) => {
          const next = { ...all, [terminal.id]: {
            label: terminal.label, machine: terminal.machine, where: "",
            state: terminal.state, away: false, running: false } };
          if (instead) delete next[instead];
          return next;
        });
        setTabs((ts) => (instead && ts.includes(instead)
          ? ts.map((id) => (id === instead ? terminal.id : id))
          : withTab(ts, terminal.id)));
        navigate(`/t/${terminal.id}`,
          !!instead && location.pathname === `/t/${instead}`);
        refresh();
      })
      .catch((e: Error) => notYet(`Could not open a terminal: ${e.message}`));
  }, [navigate, refresh, notYet]);

  const closeTab = useCallback((id: string) => {
    const at = shownTabs.indexOf(id);
    const rest = shownTabs.filter((x) => x !== id);
    setTabs(rest);
    setStatuses((all) => {
      const others = { ...all };
      delete others[id];
      return others;
    });
    if (id === terminalId) {
      const next = rest[at] ?? rest[at - 1];
      navigate(next ? `/t/${next}` : "/");
    }
  }, [shownTabs, terminalId, navigate]);

  const toggleTheme = useCallback(() => {
    const next: Theme = shown(theme) === "light" ? "dark" : "light";
    setTheme(next);
    saveTheme(next).catch(() =>
      notYet("Could not save the theme: tTerm Web did not answer."));
  }, [theme, notYet]);

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

  // The palette: Cmd+K on a Mac, Ctrl+Shift+K elsewhere, before the
  // terminal hears it.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!shortcut(event, "k")) return;
      event.preventDefault();
      event.stopPropagation();
      setPicking(false);
      setPalette((was) => !was);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  useEffect(() => () => {
    cancelTimers();
    window.clearTimeout(toastTimer.current);
  }, []);

  // Each tab as the bar shows it.
  const live = useMemo(() => state?.terminals ?? [], [state]);
  const tabViews: TabView[] = shownTabs.map((id) => {
    const status = statuses[id];
    const known = live.find((t) => t.id === id);
    const label = status?.label ?? known?.label ?? "Terminal";
    const machine = status?.machine || known?.machine || "its machine";
    const dot = dotOf(status, known, !!state && !known && !status);
    const where = status?.where ? ` · ${status.where}` : "";
    const title = dot === "away"
      ? `${label}: ${machine} is asleep or offline, the terminal waits for it`
      : dot === "ended" ? `${label}: ended`
      : dot === "busy" && status?.running ? `${label}${where}: a command is running`
      : `${label}${where}`;
    return { id, label, dot, title };
  });
  const current = tabViews.find((t) => t.id === terminalId);
  const title = current ? `${current.label} · tTerm` : "tTerm";
  useEffect(() => {
    document.title = title;
  }, [title]);

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
  const canAddTo = state.projects.find((p) =>
    p.role === "owner" || p.role === "admin");
  const anyMachine = state.projects.some((p) => p.machines.length > 0);
  const startAdding = (projectId: string, kind: MachineKind = "server") => {
    if (!pinned) setOpen(false);
    setAdding({ projectId, kind });
  };
  const goTo = (id: string) => navigate(`/t/${id}`);
  const dotFor = (t: TerminalInfo): Dot =>
    dotOf(statuses[t.id], t, false);

  const sidebarButton = !pinned && (
    <button type="button" className="icon-button"
      aria-label="Open sidebar" aria-expanded={sidebarShown}
      onClick={() => (sidebarShown ? closeSidebar() : setOpen(true))}
      onMouseEnter={hoverOpen}
      onMouseLeave={() => window.clearTimeout(openTimer.current)}>
      <ChevronRight />
    </button>
  );

  // What "+" and the palette offer: a new terminal on any machine.
  const machineGroups = (): FinderGroup[] => state.projects
    .filter((p) => p.machines.length > 0)
    .map((p) => ({
      title: `${p.code} · ${p.name}`,
      items: p.machines.map((m): FinderItem => {
        const offline = m.kind === "computer" && !m.online;
        return {
          key: `m-${m.id}`,
          label: `${p.code}/${m.name}`,
          hint: offline ? "offline" : m.os ?? m.host ?? undefined,
          icon: m.kind === "server" ? <Server size={15} /> : <Computer size={15} />,
          muted: offline,
          words: [m.name, m.host, m.os, m.kind].filter(Boolean).join(" "),
          run: () => (offline
            ? notYet(`${m.name} is offline: start its agent first.`)
            : startTerminal(m.id)),
        };
      }),
    }));
  const addItem: FinderItem[] = canAddTo ? [{
    key: "add-machine",
    label: "Add a machine",
    hint: "a server or a computer",
    icon: <Plus size={15} />,
    words: "new server computer connect",
    run: () => startAdding(canAddTo.id),
  }] : [];

  const paletteGroups = (): FinderGroup[] => {
    const listed = new Set(shownTabs);
    const terminals: FinderItem[] = [
      ...tabViews.map((t): FinderItem => ({
        key: `t-${t.id}`,
        label: t.label,
        hint: t.dot === "away" ? "waits for its computer"
          : t.dot === "ended" ? "ended" : t.id === terminalId ? "this tab" : "tab",
        icon: <span className={`dot ${t.dot}`} aria-hidden="true" />,
        words: "terminal tab go",
        run: () => goTo(t.id),
      })),
      ...live.filter((t) => !listed.has(t.id)).map((t): FinderItem => ({
        key: `t-${t.id}`,
        label: t.label,
        hint: t.away ? "waits for its computer" : "running, no tab",
        icon: <span className={`dot ${dotFor(t)}`} aria-hidden="true" />,
        words: "terminal go",
        run: () => goTo(t.id),
      })),
    ];
    const actions: FinderItem[] = [
      ...addItem,
      {
        key: "machines",
        label: "All machines",
        icon: <Server size={15} />,
        words: "machines home overview",
        run: () => navigate("/"),
      },
      ...(terminalId ? [{
        key: "close-tab",
        label: "Close this tab",
        hint: "the terminal keeps running",
        icon: <Close size={15} />,
        words: "tab close",
        run: () => closeTab(terminalId),
      }] : []),
      {
        key: "theme",
        label: shownTheme === "light" ? "Switch to the dark theme"
          : "Switch to the light theme",
        icon: shownTheme === "light" ? <Moon size={15} /> : <Sun size={15} />,
        words: "theme dark light colours colors",
        run: toggleTheme,
      },
      {
        key: "pin",
        label: pinned ? "Let the sidebar slide away" : "Keep the sidebar open",
        icon: <Pin size={15} />,
        words: "sidebar panel pin",
        run: togglePin,
      },
    ];
    return [
      { title: "Terminals", items: terminals },
      { title: "New terminal", items: machineGroups().flatMap((g) => g.items) },
      { title: "Actions", items: actions },
    ];
  };

  return (
    <div className={pinned ? "app pinned" : "app"}>
      <TabBar sidebarButton={sidebarButton} tabs={tabViews}
        current={terminalId} onSelect={goTo} onClose={closeTab}
        picking={picking}
        onPlus={() => {
          setPalette(false);
          setPicking((was) => !was);
        }}
        renderPicker={(around) => (
          <Finder className="machine-picker" label="New terminal"
            placeholder="A machine in any project" around={around}
            groups={[...machineGroups(),
              ...(addItem.length ? [{ title: "More", items: addItem }] : [])]}
            onClose={() => setPicking(false)} />
        )}
        onJump={() => {
          setPicking(false);
          setPalette(true);
        }} />

      <div className="stage">
        {shownTabs.map((id) => (
          <TerminalView key={id} terminalId={id} active={id === terminalId}
            shownTheme={shownTheme} onStatus={onStatus} onCloseTab={closeTab}
            onOpenNew={startTerminal} onChanged={refresh} notify={notYet} />
        ))}
        {!terminalId && (
          <div className="page-scroll">
            {anyMachine ? (
              <Machines projects={state.projects} terminals={state.terminals}
                onAdd={(projectId) => startAdding(projectId)}
                onOpenTerminal={startTerminal} onGoTo={goTo} />
            ) : (
              <Welcome me={state.me} personal={personal}
                onAdd={(kind) => personal && startAdding(personal.id, kind)} />
            )}
          </div>
        )}
        {!online && shownTabs.length > 0 && (
          <div className="lost-strip" role="status">
            Connection lost. Reconnecting; your terminals keep running.
          </div>
        )}
      </div>

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
          currentTerminal={terminalId ?? undefined}
          onOpenTerminal={(machineId) => {
            if (!pinned) setOpen(false);
            startTerminal(machineId);
          }}
          onGoTo={(id) => {
            if (!pinned) setOpen(false);
            goTo(id);
          }}
          onHome={() => {
            if (!pinned) setOpen(false);
            navigate("/");
          }}
          dotFor={dotFor}
          notYet={notYet}
        />
      )}

      {palette && (
        <div className="palette-backdrop">
          <Finder className="palette" label="Jump to"
            placeholder="A terminal, a machine or something to do"
            groups={paletteGroups()} onClose={() => setPalette(false)} />
        </div>
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
