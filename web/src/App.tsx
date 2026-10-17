import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Account } from "./Account";
import { AddMachine } from "./AddMachine";
import {
  endTerminal, loadState, onSignedOut, openTerminal, removeMachine, saveTheme,
  SignedOut, signOut, type Machine, type MachineKind, type State,
  type TerminalInfo, type Theme,
} from "./api";
import { Confirm, Removed } from "./Confirm";
import { Finder, type FinderGroup, type FinderItem } from "./Finder";
import {
  ChevronRight, Close, Computer, Moon, Person, Pin, Plus, Server, Sun,
} from "./icons";
import { Machines } from "./Machines";
import { Sidebar } from "./Sidebar";
import { SignIn } from "./SignIn";
import { socket } from "./socket";
import { TabBar, type Dot, type TabView } from "./TabBar";
import { rememberedTabs, rememberTabs, shortcut, TABS_MAX } from "./tabs";
import { TerminalView, type TabStatus } from "./TerminalView";
import {
  applyTheme, onSystemThemeChange, rememberedPin, rememberPin, shown,
  type Shown,
} from "./theme";
import { lastSeen } from "./time";
import { Welcome } from "./Welcome";

/** Hovering the ⟩ opens the sidebar after this pause, so that a mouse merely
 * passing by does not throw it open. */
const OPEN_DELAY_MS = 180;
/** Leaving the sidebar closes it after this pause, forgiving a sloppy mouse. */
const CLOSE_DELAY_MS = 250;
const TOAST_MS = 3200;
/** How often the page asks whether computers came online or went. */
const COMPUTERS_MS = 10_000;

/** /t/<id> is a terminal, /account the account page (0.0.10); anything else
 * is the machines page. */
const TERMINAL_PATH = /^\/t\/([0-9a-f-]{36})\/?$/;
const ACCOUNT_PATH = /^\/account\/?$/;

/** The sign-in screen's note when a session ends with nothing done here:
 * eight hours away, or signed out from another device. */
const SESSION_ENDED = "Your session ended. Sign in again: your terminals " +
  "kept running.";

/** The core is on this very computer (the owner's laptop): then it is the
 * window running it that is worth a look. */
const LOCAL = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);

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

/** The account page's step in the history: where it was opened from. */
interface AccountMark {
  accountFrom: string;
}

/** What waits for a yes before it is done. */
type Asking =
  | { kind: "remove"; machine: Machine }
  | { kind: "end"; terminal: TerminalInfo };

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
  const [asking, setAsking] = useState<Asking | null>(null);
  const [removed, setRemoved] = useState<{ name: string; cleanup: string } | null>(null);
  /** Not signed in: the sign-in screen, with a note on why, if there is one. */
  const [signedOut, setSignedOut] = useState<{ note: string | null } | null>(null);
  const openTimer = useRef<number | undefined>(undefined);
  const closeTimer = useRef<number | undefined>(undefined);
  const toastTimer = useRef<number | undefined>(undefined);
  const pruned = useRef(false);
  /** Who was signed in on this page last, and with which address. */
  const lastUser = useRef<string | null>(null);
  const lastEmail = useRef<string | null>(null);
  /** Each opening of the account page shows it afresh: devices that came
   * or went meanwhile, what the plan counts now. */
  const [accountVisit, setAccountVisit] = useState(0);
  const stateNow = useRef<State | null>(null);
  stateNow.current = state;

  const load = useCallback(() => {
    setProblem(null);
    loadState()
      .then((loaded) => {
        if (lastUser.current && lastUser.current !== loaded.me.id) {
          // Someone else signed in on this page: nothing of the one before.
          pruned.current = true;
          setTabs([]);
          setStatuses({});
          history.replaceState(null, "", "/");
          setPath("/");
        }
        lastUser.current = loaded.me.id;
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
      .catch((error) => {
        // Not signed in: onSignedOut below shows the sign-in screen.
        if (error instanceof SignedOut) return;
        setProblem(LOCAL
          ? "tTerm Web is not answering. Is python -m tterm.dev running in " +
            "a terminal window?"
          : "tTerm Web is not answering. Check the connection, then try " +
            "again.");
      });
  }, []);

  useEffect(load, [load]);

  /** The session is over: the sign-in screen, and nothing of the page
   * behind it. A note from Sign out itself wins over one from the socket
   * closing because of it. */
  const endSession = useCallback((note: string | null, firm = true) => {
    lastEmail.current = stateNow.current?.me.email ?? lastEmail.current;
    setSignedOut((was) => (firm || !was ? { note } : was));
    setState(null);
    setAdding(null);
    setAsking(null);
    setRemoved(null);
    setPalette(false);
    setPicking(false);
  }, []);

  useEffect(() => onSignedOut(() =>
    endSession(lastUser.current ? SESSION_ENDED : null, false)), [endSession]);

  const signedIn = useCallback(() => {
    socket.signedIn();
    setSignedOut(null);
    pruned.current = false;
    load();
  }, [load]);

  /** After a machine connects or a terminal opens or ends: the sidebar and
   * the page learn of it. Asked in the background: it is not something the
   * person did, and keeps no session on. */
  const refresh = useCallback(() => {
    loadState(true).then(setState).catch(() => {});
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
  const navigate = useCallback((to: string, inPlace = false,
    mark: AccountMark | null = null) => {
    if (to !== location.pathname) {
      if (inPlace) history.replaceState(mark, "", to);
      else history.pushState(mark, "", to);
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

  // Removing a machine and ending a terminal ask first (Confirm).
  const askRemove = useCallback((machine: Machine) => {
    setPicking(false);
    setPalette(false);
    setAsking({ kind: "remove", machine });
  }, []);
  const askEnd = useCallback((terminal: TerminalInfo) => {
    setPicking(false);
    setPalette(false);
    setAsking({ kind: "end", terminal });
  }, []);
  const remove = useCallback(async (machine: Machine) => {
    // A refusal comes back as an error, which the dialog shows.
    const result = await removeMachine(machine.id);
    setAsking(null);
    refresh();
    if (result.cleanup) setRemoved({ name: result.name, cleanup: result.cleanup });
    else if (result.shared) {
      notYet(`${result.name} is removed from tTerm Web. The same server is in ` +
        "another project too, so tTerm Web's access on it stays.");
    } else notYet(`${result.name} is removed from tTerm Web.`);
  }, [refresh, notYet]);
  const end = useCallback(async (terminal: TerminalInfo) => {
    await endTerminal(terminal.id);
    setAsking(null);
    refresh();
  }, [refresh]);

  /** Sign out, on the account page only: this browser forgets the person. */
  const signOutHere = useCallback(() => signOut()
    .then(() => endSession("You signed out."))
    .catch((error) => {
      notYet("Could not sign out: tTerm Web did not answer. Try again.");
      throw error;
    }), [endSession, notYet]);

  const chooseTheme = useCallback((next: Theme) => {
    setTheme(next);
    saveTheme(next).catch(() =>
      notYet("Could not save the theme: tTerm Web did not answer."));
  }, [notYet]);
  const toggleTheme = useCallback(() => {
    chooseTheme(shown(theme) === "light" ? "dark" : "light");
  }, [theme, chooseTheme]);

  /** The name changed on the account page: the sidebar and the greetings
   * follow at once. */
  const renamed = useCallback((named: {
    name: string; firstName: string; initials: string;
  }) => {
    setState((was) => (was ? { ...was, me: { ...was.me, ...named } } : was));
  }, []);

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
  const accountOpen = !terminalId && ACCOUNT_PATH.test(path);
  const title = current ? `${current.label} · tTerm`
    : accountOpen ? "Account · tTerm" : "tTerm";
  useEffect(() => {
    document.title = title;
  }, [title]);

  if (signedOut) {
    return (
      <SignIn note={signedOut.note} email={lastEmail.current ?? undefined}
        onSignedIn={signedIn} />
    );
  }

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
  const openAccount = () => {
    if (!pinned) setOpen(false);
    setPalette(false);
    setPicking(false);
    setAccountVisit((n) => n + 1);
    if (!ACCOUNT_PATH.test(location.pathname)) {
      navigate("/account", false, { accountFrom: location.pathname });
    }
  };
  /** Back to where the page was opened from: the step before it in the
   * history, which its entry remembers (Back and Forward keep that right).
   * Opened by its address, as a bookmark, to the machines page instead,
   * never out of tTerm. */
  const closeAccount = () => {
    const mark = history.state as AccountMark | null;
    if (mark?.accountFrom) history.back();
    else navigate("/", true);
  };
  const runningHere = terminalId ? live.find((t) => t.id === terminalId) : undefined;
  const machineIds = new Set(state.projects.flatMap((p) => p.machines.map((m) => m.id)));
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
          hint: offline ? ["offline", lastSeen(m.lastSeen)].filter(Boolean).join(", ")
            : m.os ?? m.host ?? undefined,
          icon: m.kind === "server" ? <Server size={15} /> : <Computer size={15} />,
          muted: offline,
          words: [m.name, m.host, m.os, m.kind].filter(Boolean).join(" "),
          run: () => (offline
            ? notYet(`${m.name} is offline: it comes back when its agent connects.`)
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
      ...(runningHere ? [{
        key: "end-terminal",
        label: "End this terminal",
        hint: "its shell ends",
        icon: <Close size={15} />,
        words: "terminal end stop kill exit shell",
        run: () => askEnd(runningHere),
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
      {
        key: "account",
        label: "Account",
        hint: "name, plan, devices, sign out",
        icon: <Person size={15} />,
        words: "account profile name plan devices quick code forget theme " +
          "sign out log out logout settings",
        run: openAccount,
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
            onOpenNew={startTerminal} onChanged={refresh} notify={notYet}
            machineThere={(machineId) => machineIds.has(machineId)} />
        ))}
        {accountOpen && (
          <div className="page-scroll">
            <Account key={accountVisit} me={state.me} theme={theme}
              onTheme={chooseTheme}
              onRenamed={renamed} onSignOut={signOutHere}
              onSignedOut={(note) => endSession(note)} onClose={closeAccount}
              notify={notYet} />
          </div>
        )}
        {!terminalId && !accountOpen && (
          <div className="page-scroll">
            {anyMachine ? (
              <Machines projects={state.projects} terminals={state.terminals}
                onAdd={(projectId) => startAdding(projectId)}
                onOpenTerminal={startTerminal} onGoTo={goTo}
                onRemove={askRemove} />
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
          onRemoveMachine={askRemove}
          onEndTerminal={askEnd}
          onAccount={openAccount}
          accountOpen={accountOpen}
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

      {asking?.kind === "remove" && (
        <Confirm title={`Remove ${asking.machine.name}?`}
          confirm={`Remove ${asking.machine.name}`}
          onConfirm={() => remove(asking.machine)}
          onCancel={() => setAsking(null)}>
          <p>
            It disappears from tTerm Web, and its terminals end. The commands
            typed there stay in the history.
          </p>
          {asking.machine.kind === "computer" ? (
            <p className="hint">
              Its agent hears that the computer is removed and stops. To
              connect the computer again: Add a machine, then Computer.
            </p>
          ) : (
            <p className="hint">
              tTerm Web stops signing in to it. On the server itself, tTerm
              Web's access stays until you take it back there: the command for
              that comes next, unless the server is in another project too.
            </p>
          )}
        </Confirm>
      )}

      {asking?.kind === "end" && (
        <Confirm title={`End ${asking.terminal.label}?`} confirm="End the terminal"
          onConfirm={() => end(asking.terminal)}
          onCancel={() => setAsking(null)}>
          <p>
            Its shell ends, and whatever runs in it stops, as when a terminal
            window is closed. What it ran stays in its tab and in the history.
          </p>
        </Confirm>
      )}

      {removed && (
        <Removed name={removed.name} cleanup={removed.cleanup}
          onClose={() => setRemoved(null)} notify={notYet} />
      )}

      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
