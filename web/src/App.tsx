import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Account } from "./Account";
import { AddMachine } from "./AddMachine";
import {
  CoreError, createFolder, createNote, deleteFolder, deleteNote, endTerminal,
  loadNote, loadState, moveNote, NoteGone, onSignedOut, openTerminal,
  removeMachine, renameFolder, saveTheme, SignedOut, signOut, type Folder,
  type Machine, type MachineKind, type Note, type NoteSummary, type Project,
  type State, type TerminalInfo, type Theme,
} from "./api";
import { Confirm, Removed } from "./Confirm";
import { Finder, type FinderGroup, type FinderItem } from "./Finder";
import {
  ChevronRight, Close, Computer, Doc, Folder as FolderIcon, ListCheck, Moon,
  Person, Pin, Plus, Server, Sun, Trash,
} from "./icons";
import { Machines } from "./Machines";
import { FolderDialog } from "./Menus";
import { draftsOf, forgetDrafts, markDeleted } from "./drafts";
import { NoteView, primeNote, type SaveState, type Typed } from "./NoteView";
import { summary, titled, toMarkdown } from "./noteText";
import { ProjectPage } from "./ProjectPage";
import { deletesNote, listed, many } from "./rights";
import { Sidebar } from "./Sidebar";
import { SignIn } from "./SignIn";
import { socket } from "./socket";
import { TabBar, type Dot, type SaveLine, type TabView } from "./TabBar";
import {
  isTerminalTab, noteTab, projectTab, rememberedFolds, rememberedTabs,
  rememberFolds, rememberTabs, shortcut, tabKind, tabOfPath, tabPath,
  tabTarget, TABS_MAX,
} from "./tabs";
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
/** Without computers, how often it asks what changed elsewhere: notes
 * written on another device, say. */
const LISTS_MS = 60_000;

/** /t/<id> is a terminal, /n/<id> a note, /p/<id> a project's page (tabs.ts),
 * /account the account page (0.0.10); anything else is the machines page. */
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
  | { kind: "end"; terminal: TerminalInfo }
  | { kind: "note"; noteId: string; title: string; folder: Folder; project: Project }
  | { kind: "folder"; folder: Folder; project: Project };

/** The window that names a folder: a new one, or one renamed. */
type Naming =
  | { kind: "new"; project: Project }
  | { kind: "rename"; folder: Folder; project: Project };

/** What lists show of a note while it is open, ahead of the lists from the
 * core: its title as it is typed, and who saved it last, as of a version. */
type Live = Partial<Typed & Pick<NoteSummary, "editedAt" | "editedBy">>;

function sameStatus(a: TabStatus | undefined, b: TabStatus): boolean {
  return !!a && a.label === b.label && a.machine === b.machine &&
    a.where === b.where && a.state === b.state && a.away === b.away &&
    a.running === b.running;
}

/** A note as lists show it, from the whole of it. */
function listedNote(note: Note): NoteSummary {
  return {
    id: note.id, title: note.title, ...summary(note.blocks), version: note.version,
    createdBy: note.createdBy, createdAt: note.createdAt,
    editedBy: note.editedBy, editedAt: note.editedAt,
  };
}

function byName(a: Folder, b: Folder): number {
  return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
}

/** The state with one project's folders changed. */
function withFolders(state: State, projectId: string,
  change: (folders: Folder[]) => Folder[]): State {
  return { ...state, projects: state.projects.map((p) =>
    (p.id === projectId ? { ...p, folders: change(p.folders) } : p)) };
}

/** The state with every folder changed the same way. */
function eachFolder(state: State, change: (folder: Folder) => Folder): State {
  return { ...state, projects: state.projects.map((p) =>
    ({ ...p, folders: p.folders.map(change) })) };
}

/** The note moved in the lists: out of wherever it was, into its folder. */
function moveListed(state: State, note: Note): State {
  let entry: NoteSummary | undefined;
  const without = eachFolder(state, (f) => {
    const found = f.notes.find((n) => n.id === note.id);
    if (!found) return f;
    entry = found;
    return { ...f, notes: f.notes.filter((n) => n.id !== note.id) };
  });
  const item = entry ?? listedNote(note);
  return eachFolder(without, (f) => (f.id === note.folderId
    ? { ...f, notes: [...f.notes, item].sort((a, b) => b.createdAt - a.createdAt) }
    : f));
}

/** Where the lists have a note: its folder and its project. */
function findNote(projects: Project[], noteId: string):
  { note: NoteSummary; folder: Folder; project: Project } | null {
  for (const project of projects) {
    for (const folder of project.folders) {
      const note = folder.notes.find((n) => n.id === noteId);
      if (note) return { note, folder, project };
    }
  }
  return null;
}

/** The projects as the core sent them, with what open notes have typed:
 * unless the lists know a newer version, saved elsewhere, which a note
 * behind its tab has not seen yet. */
function withLive(projects: Project[], live: Record<string, Live>): Project[] {
  if (!Object.keys(live).length) return projects;
  const ahead = (n: NoteSummary) => {
    const l = live[n.id];
    return !!l && (l.version ?? 0) >= n.version;
  };
  return projects.map((p) => ({
    ...p,
    folders: p.folders.map((f) => (f.notes.some(ahead) ? {
      ...f, notes: f.notes.map((n) => (ahead(n) ? { ...n, ...live[n.id] } : n)),
    } : f)),
  }));
}

/** What to unfold in the sidebar to see the tab's terminal, note or project. */
function revealKeys(tab: string, state: State): string[] {
  const id = tabTarget(tab);
  const kind = tabKind(tab);
  if (kind === "project") return [`p:${id}`];
  if (kind === "note") {
    const where = findNote(state.projects, id);
    return where ? [`p:${where.project.id}`, `f:${where.folder.id}`] : [];
  }
  const terminal = state.terminals.find((t) => t.id === id);
  const project = terminal && state.projects.find((p) =>
    p.machines.some((m) => m.id === terminal.machineId));
  return terminal && project ? [`p:${project.id}`, `m:${terminal.machineId}`] : [];
}

/** Copies text that is still on its way. Safari lets a page copy only
 * within the click itself, which a promise in a ClipboardItem keeps. */
async function copyLater(text: Promise<string>): Promise<void> {
  if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
    try {
      await navigator.clipboard.write([new ClipboardItem({
        "text/plain": text.then((t) => new Blob([t], { type: "text/plain" })),
      })]);
      return;
    } catch (error) {
      if (error instanceof CoreError) throw error;
      // The browser takes text only: below.
    }
  }
  await navigator.clipboard.writeText(await text);
}

/** How saving goes in the note in front, for the line on top. */
function saveLine(state: SaveState | undefined): SaveLine | null {
  if (state === "saved") return { text: "Saved", kind: "saved" };
  if (state === "saving" || state === "unsaved") return { text: "Saving…", kind: "saving" };
  if (state === "offline") return { text: "Not saved: no connection", kind: "warn" };
  if (state === "refused" || state === "conflict") return { text: "Not saved", kind: "warn" };
  return null;
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
  const [naming, setNaming] = useState<Naming | null>(null);
  const [removed, setRemoved] = useState<{ name: string; cleanup: string } | null>(null);
  const [folded, setFolded] = useState<Set<string>>(rememberedFolds);
  const [live, setLive] = useState<Record<string, Live>>({});
  const [saveStates, setSaveStates] = useState<Record<string, SaveState>>({});
  /** Not signed in: the sign-in screen, with a note on why, if there is one. */
  const [signedOut, setSignedOut] = useState<{ note: string | null } | null>(null);
  const openTimer = useRef<number | undefined>(undefined);
  const closeTimer = useRef<number | undefined>(undefined);
  const toastTimer = useRef<number | undefined>(undefined);
  const pruned = useRef(false);
  /** Notes made on this page: their tab puts the caret in the title. */
  const fresh = useRef(new Set<string>());
  /** The tab last shown in the sidebar, unfolded. Not the one the page
   * opens with: what was folded stays so over a reload. */
  const revealed = useRef<string | null>(tabOfPath(location.pathname));
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
        // Drafts of notes are this person's only.
        draftsOf(loaded.me.id);
        if (lastUser.current && lastUser.current !== loaded.me.id) {
          // Someone else signed in on this page: nothing of the one before.
          pruned.current = true;
          setTabs([]);
          setStatuses({});
          setLive({});
          setSaveStates({});
          history.replaceState(null, "", "/");
          setPath("/");
        }
        lastUser.current = loaded.me.id;
        setState(loaded);
        setTheme(loaded.me.theme);
        if (!pruned.current) {
          // Tabs left from before: those of terminals still running, and of
          // notes and projects still there, stay.
          pruned.current = true;
          const terminals = new Set(loaded.terminals.map((t) => t.id));
          const notes = new Set(loaded.projects.flatMap((p) =>
            p.folders.flatMap((f) => f.notes.map((n) => n.id))));
          const projects = new Set(loaded.projects.map((p) => p.id));
          const here = tabOfPath(location.pathname);
          setTabs((ts) => ts.filter((tab) => {
            if (tab === here) return true;
            const kind = tabKind(tab);
            if (kind === "terminal") return terminals.has(tab);
            return (kind === "note" ? notes : projects).has(tabTarget(tab));
          }));
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
    setNaming(null);
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

  /** After a machine connects, a terminal opens or ends, a note or a folder
   * changes: the sidebar and the pages learn of it. Asked in the
   * background: it is not something the person did, and keeps no session
   * on. */
  const asked = useRef(0);
  /** Notes whose tab closed and whose last save is done: the next lists
   * from the core have them as they are. */
  const settled = useRef(new Set<string>());
  const refresh = useCallback(() => {
    const mine = ++asked.current;
    const done = [...settled.current];
    return loadState(true)
      .then((loaded) => {
        // An answer overtaken by a later question is old news: a note just
        // made, moved or deleted would come back from it for a while.
        if (mine !== asked.current) return;
        setState(loaded);
        if (!done.length) return;
        for (const id of done) settled.current.delete(id);
        const open = new Set(shownRef.current.filter((tab) => tabKind(tab) === "note")
          .map(tabTarget));
        const drop = done.filter((id) => !open.has(id));
        if (!drop.length) return;
        const without = <T,>(was: Record<string, T>) => {
          const rest = { ...was };
          for (const id of drop) delete rest[id];
          return rest;
        };
        setLive(without);
        setSaveStates(without);
      })
      .catch(() => {});
  }, []);

  // A computer comes online when its agent starts and goes when it stops,
  // with nothing done on this page; notes change on other devices. Ask now
  // and then, often while there are computers, and whenever the window
  // comes back into view.
  const ready = !!state;
  const computers = !!state?.projects.some((p) =>
    p.machines.some((m) => m.kind === "computer"));
  useEffect(() => {
    if (!ready) return;
    const timer = window.setInterval(refresh, computers ? COMPUTERS_MS : LISTS_MS);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [ready, computers, refresh]);

  // Addresses: back and forward move between the machines page and tabs
  // the way they would between pages. In place, the address takes the
  // current one's step in the history instead of adding one.
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

  // A terminal, a note or a project's page opened by its address gets a
  // tab; the tabs are remembered.
  const current = tabOfPath(path);
  useEffect(() => {
    if (current) setTabs((ts) => withTab(ts, current));
  }, [current]);
  useEffect(() => rememberTabs(tabs), [tabs]);
  const shownTabs = current ? withTab(tabs, current) : tabs;
  const shownRef = useRef(shownTabs);
  shownRef.current = shownTabs;
  const currentRef = useRef(current);
  currentRef.current = current;

  // What is folded in the sidebar, remembered.
  useEffect(() => rememberFolds(folded), [folded]);
  const fold = useCallback((key: string) => setFolded((was) => {
    const next = new Set(was);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  }), []);
  const unfold = useCallback((...keys: string[]) => setFolded((was) =>
    (keys.some((k) => was.has(k)) ? new Set([...was].filter((k) => !keys.includes(k)))
      : was)), []);

  // The tab in front shows in the sidebar, unfolded down to it.
  useEffect(() => {
    if (!state || !current || revealed.current === current) return;
    const keys = revealKeys(current, state);
    if (!keys.length) return;
    revealed.current = current;
    unfold(...keys);
  }, [current, state, unfold]);

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

  /** A tab in front: opened if it was not. */
  const openTab = useCallback((tab: string) => {
    setTabs((ts) => withTab(ts, tab));
    navigate(tabPath(tab));
  }, [navigate]);

  /** Closes tabs. The one in front gives way to its neighbour, the one after
   * it or else before it; `inPlace` when what was in it is gone, so that
   * Back does not bring it up again. */
  const closeTabs = useCallback((gone: string[], inPlace = false) => {
    const all = shownRef.current;
    setTabs(all.filter((x) => !gone.includes(x)));
    setStatuses((was) => {
      if (!gone.some((g) => was[g])) return was;
      const rest = { ...was };
      for (const g of gone) delete rest[g];
      return rest;
    });
    const now = currentRef.current;
    if (now && gone.includes(now)) {
      const at = all.indexOf(now);
      const next = all.slice(at + 1).find((x) => !gone.includes(x)) ??
        all.slice(0, Math.max(at, 0)).reverse().find((x) => !gone.includes(x));
      navigate(next ? tabPath(next) : "/", inPlace);
    }
  }, [navigate]);
  const closeTab = useCallback((tab: string) => closeTabs([tab]), [closeTabs]);

  /** A new terminal on the machine, in a new tab. Opened from an ended
   * terminal (`instead`), it takes that one's tab and its address: Back
   * does not bring the ended one back. */
  const startTerminal = useCallback((machineId: string, instead?: string) => {
    const { cols, rows } = guessSize();
    const project = stateNow.current?.projects.find((p) =>
      p.machines.some((m) => m.id === machineId));
    if (project) unfold(`p:${project.id}`, `m:${machineId}`);
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
  }, [navigate, refresh, notYet, unfold]);

  /** A new terminal from a machine's "+": an offline computer says so. */
  const newTerminal = useCallback((machine: Machine) => {
    if (machine.kind === "computer" && !machine.online) {
      const seen = lastSeen(machine.lastSeen);
      notYet(`${machine.name} is offline${seen ? `, ${seen}` : ""}: it comes ` +
        "back when its agent connects.");
      return;
    }
    startTerminal(machine.id);
  }, [notYet, startTerminal]);

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
    } else if (result.accessRemoved) {
      notYet(`${result.name} is removed from tTerm Web, and tTerm Web's ` +
        "access on the server is taken back.");
    } else notYet(`${result.name} is removed from tTerm Web.`);
  }, [refresh, notYet]);
  const end = useCallback(async (terminal: TerminalInfo) => {
    await endTerminal(terminal.id);
    setAsking(null);
    refresh();
  }, [refresh]);

  // ------------------------------------------------------- notes (0.1.0)

  /** A new note in the folder, in a tab of its own, the caret in its title. */
  const newNote = useCallback((folderId: string) => {
    createNote(folderId)
      .then((note) => {
        primeNote(note);
        fresh.current.add(note.id);
        setState((s) => s && withFolders(s, note.projectId, (fs) => fs.map((f) =>
          (f.id === folderId ? { ...f, notes: [listedNote(note),
            ...f.notes.filter((n) => n.id !== note.id)] } : f))));
        unfold(`p:${note.projectId}`, `f:${folderId}`);
        openTab(noteTab(note.id));
        refresh();
      })
      .catch((e: Error) => notYet(`Could not start a note: ${e.message}`));
  }, [notYet, openTab, refresh, unfold]);

  const saveFolder = useCallback(async (asked: Naming, name: string) => {
    // A refusal comes back as an error, which the window shows.
    if (asked.kind === "new") {
      const folder = await createFolder(asked.project.id, name);
      setState((s) => s && withFolders(s, asked.project.id, (fs) =>
        [...fs.filter((f) => f.id !== folder.id), folder].sort(byName)));
      unfold(`p:${asked.project.id}`, `f:${folder.id}`);
    } else {
      const renamed = await renameFolder(asked.folder.id, name);
      setState((s) => s && withFolders(s, asked.project.id, (fs) =>
        fs.map((f) => (f.id === renamed.id ? { ...f, name: renamed.name } : f))
          .sort(byName)));
    }
    setNaming(null);
    refresh();
  }, [refresh, unfold]);

  const askDeleteNote = useCallback((noteId: string, title?: string) => {
    const where = stateNow.current && findNote(stateNow.current.projects, noteId);
    if (!where) return;
    setPalette(false);
    setAsking({ kind: "note", noteId, title: title ?? where.note.title,
      folder: where.folder, project: where.project });
  }, []);
  const removeNote = useCallback(async (asked: { noteId: string; folder: Folder;
    project: Project }) => {
    const result = await deleteNote(asked.noteId);
    setAsking(null);
    markDeleted(asked.noteId);
    closeTabs([noteTab(asked.noteId)], true);
    setState((s) => s && eachFolder(s, (f) =>
      ({ ...f, notes: f.notes.filter((n) => n.id !== asked.noteId) })));
    notYet(`The note ${titled(result.title)} is deleted from ` +
      `${asked.folder.name}.${asked.project.personal ? ""
        : " The journal has a line about it."}`);
    refresh();
  }, [closeTabs, notYet, refresh]);

  const askDeleteFolder = useCallback((folder: Folder, project: Project) => {
    setAsking({ kind: "folder", folder, project });
  }, []);
  const removeFolder = useCallback(async (asked: { folder: Folder;
    project: Project }) => {
    const result = await deleteFolder(asked.folder.id);
    setAsking(null);
    const inside = stateNow.current?.projects.flatMap((p) => p.folders)
      .find((f) => f.id === asked.folder.id)?.notes ?? asked.folder.notes;
    for (const n of inside) markDeleted(n.id);
    closeTabs(inside.map((n) => noteTab(n.id)), true);
    setState((s) => s && withFolders(s, asked.project.id, (fs) =>
      fs.filter((f) => f.id !== asked.folder.id)));
    const n = result.notes;
    notYet(`The folder ${result.name}${n === 0 ? " is deleted."
      : n === 1 ? " and its note are deleted."
      : ` and its ${n} notes are deleted.`}${asked.project.personal ? ""
        : " The journal has a line about it."}`);
    refresh();
  }, [closeTabs, notYet, refresh]);

  /** A note moved to another folder, from its tab or from the project's
   * page: the lists follow at once. */
  const moved = useCallback((note: Note) => {
    setState((s) => s && moveListed(s, note));
    unfold(`f:${note.folderId}`);
    notYet(`Moved to ${note.folder}.`);
    refresh();
  }, [notYet, refresh, unfold]);
  const moveFromList = useCallback((noteId: string, folderId: string) => {
    moveNote(noteId, folderId)
      .then(moved)
      .catch((e: Error) => notYet(`Could not move it: ${e.message}`));
  }, [moved, notYet]);

  const copyNote = useCallback((noteId: string) => {
    copyLater(loadNote(noteId).then((n) => toMarkdown(n.title, n.blocks)))
      .then(() => notYet("Copied as Markdown."))
      .catch((e: Error) => notYet(e instanceof NoteGone ? "The note is deleted."
        : e instanceof CoreError ? `Could not copy: ${e.message}`
        : "Could not copy: the browser did not let the page."));
  }, [notYet]);

  // What open notes tell of themselves.
  const onSummary = useCallback((noteId: string, typed: Typed) => {
    setLive((was) => ({ ...was, [noteId]: { ...was[noteId], ...typed } }));
  }, []);
  const onNote = useCallback((note: Note) => {
    setLive((was) => ({ ...was, [note.id]: { ...was[note.id],
      editedAt: note.editedAt, editedBy: note.editedBy,
      version: Math.max(was[note.id]?.version ?? 0, note.version) } }));
  }, []);
  const onSaveState = useCallback((noteId: string, saving: SaveState) => {
    setSaveStates((was) => (was[noteId] === saving ? was
      : { ...was, [noteId]: saving }));
  }, []);
  /** A note's tab closed and what was left in it saved: the lists from the
   * core have it now. */
  const onSettled = useCallback((noteId: string) => {
    // Opened again later, it is no longer new: the caret stays out of the
    // title.
    fresh.current.delete(noteId);
    settled.current.add(noteId);
    void refresh();
  }, [refresh]);

  /** Sign out, on the account page only: this browser forgets the person. */
  const signOutHere = useCallback(() => signOut()
    .then(() => {
      // The browser forgets the person: their unsaved notes too.
      forgetDrafts();
      endSession("You signed out.");
    })
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
      if (event.key === "Escape" && !pinned && !adding && !naming) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pinned, adding, naming]);

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

  // The lists as shown: what the core sent, with what open notes typed.
  const projects = useMemo(() => (state ? withLive(state.projects, live) : []),
    [state, live]);

  // Each tab as the bar shows it.
  const liveTerminals = useMemo(() => state?.terminals ?? [], [state]);
  const tabViews: TabView[] = shownTabs.map((tab): TabView => {
    const kind = tabKind(tab);
    const id = tabTarget(tab);
    if (kind === "note") {
      const where = findNote(projects, id);
      const title = live[id]?.title ?? where?.note.title ?? "";
      return {
        id: tab, kind, code: where?.project.code ?? "", label: titled(title),
        untitled: !title.trim(), checklist: (live[id]?.total ?? where?.note.total ?? 0) > 0,
        title: where ? `Note in ${where.folder.name}` : "Note",
        closeTitle: "Close the note. It stays in its folder.",
      };
    }
    if (kind === "project") {
      const project = projects.find((p) => p.id === id);
      return {
        id: tab, kind, code: project?.code ?? "", label: project?.name ?? "Project",
        title: project ? `${project.name}: its machines and notes` : "Project",
        closeTitle: "Close the page",
      };
    }
    const status = statuses[tab];
    const known = liveTerminals.find((t) => t.id === tab);
    const full = status?.label ?? known?.label ?? "Terminal";
    const machine = status?.machine || known?.machine || "its machine";
    const dot = dotOf(status, known, !!state && !known && !status);
    const where = status?.where ? ` · ${status.where}` : "";
    const slash = full.indexOf("/");
    return {
      id: tab, kind, code: slash > 0 ? full.slice(0, slash) : "",
      label: slash > 0 ? full.slice(slash + 1) : full, dot,
      title: dot === "away"
        ? `${full}: ${machine} is asleep or offline, the terminal waits for it`
        : dot === "ended" ? `${full}: ended`
        : dot === "busy" && status?.running ? `${full}${where}: a command is running`
        : `${full}${where}`,
      closeTitle: "Close the tab. The terminal keeps running, in the sidebar " +
        "under its machine.",
    };
  });
  const front = tabViews.find((t) => t.id === current);
  const accountOpen = !current && ACCOUNT_PATH.test(path);
  const title = front
    ? `${front.kind === "terminal" && front.code ? `${front.code}/` : ""}${front.label} · tTerm`
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
  const personal = projects.find((p) => p.personal) ??
    projects.find((p) => p.role === "owner");
  const canAddTo = projects.find((p) =>
    p.role === "owner" || p.role === "admin");
  const anyMachine = projects.some((p) => p.machines.length > 0);
  const startAdding = (projectId: string, kind: MachineKind = "server") => {
    if (!pinned) setOpen(false);
    setAdding({ projectId, kind });
  };
  const goTo = (id: string) => navigate(`/t/${id}`);
  /** From the sidebar: it slides away unless it is pinned. */
  const away = (then: () => void) => () => {
    if (!pinned) setOpen(false);
    then();
  };
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
  const frontKind = current ? tabKind(current) : null;
  const runningHere = frontKind === "terminal"
    ? liveTerminals.find((t) => t.id === current) : undefined;
  const pageProject = frontKind === "project"
    ? projects.find((p) => p.id === tabTarget(current!)) : undefined;
  const machineIds = new Set(projects.flatMap((p) => p.machines.map((m) => m.id)));
  const dotFor = (t: TerminalInfo): Dot => dotOf(statuses[t.id], t, false);

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
  const machineGroups = (): FinderGroup[] => projects
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
          run: () => newTerminal(m),
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
    const listedTabs = new Set(shownTabs);
    const terminals: FinderItem[] = [
      ...tabViews.filter((t) => t.kind === "terminal").map((t): FinderItem => ({
        key: `t-${t.id}`,
        label: `${t.code ? `${t.code}/` : ""}${t.label}`,
        hint: t.dot === "away" ? "waits for its computer"
          : t.dot === "ended" ? "ended" : t.id === current ? "this tab" : "tab",
        icon: <span className={`dot ${t.dot}`} aria-hidden="true" />,
        words: "terminal tab go",
        run: () => goTo(t.id),
      })),
      ...liveTerminals.filter((t) => !listedTabs.has(t.id)).map((t): FinderItem => ({
        key: `t-${t.id}`,
        label: t.label,
        hint: t.away ? "waits for its computer" : "running, no tab",
        icon: <span className={`dot ${dotFor(t)}`} aria-hidden="true" />,
        words: "terminal go",
        run: () => goTo(t.id),
      })),
    ];
    // The notes edited last come first; before anything is typed, only a
    // few of them.
    const notes: FinderItem[] = projects.flatMap((p) => p.folders.flatMap((f) =>
      f.notes.map((n) => ({ n, f, p }))))
      .sort((a, b) => b.n.editedAt - a.n.editedAt)
      .map(({ n, f, p }): FinderItem => ({
        key: `n-${n.id}`,
        label: `${p.code}/${titled(n.title)}`,
        hint: listedTabs.has(noteTab(n.id)) ? `${f.name} · tab` : f.name,
        icon: n.total > 0 ? <ListCheck size={15} /> : <Doc size={15} />,
        words: `note ${f.name} ${n.excerpt}`,
        run: () => openTab(noteTab(n.id)),
      }));
    const pages: FinderItem[] = projects.map((p): FinderItem => ({
      key: `p-${p.id}`,
      label: p.name,
      hint: "machines and notes",
      icon: <span className="code-chip" aria-hidden="true">{p.code}</span>,
      words: `project page ${p.code} folders`,
      run: () => openTab(projectTab(p.id)),
    }));
    const noteHere = frontKind === "note" ? findNote(projects, tabTarget(current!)) : null;
    const actions: FinderItem[] = [
      ...addItem,
      ...projects.filter((p) => p.role !== "observer").map((p): FinderItem => ({
        key: `folder-${p.id}`,
        label: projects.length > 1 ? `New folder in ${p.name}` : "New folder",
        hint: "for notes",
        icon: <FolderIcon size={15} />,
        words: "notes folder new create",
        run: () => setNaming({ kind: "new", project: p }),
      })),
      {
        key: "machines",
        label: "All machines",
        icon: <Server size={15} />,
        words: "machines home overview",
        run: () => navigate("/"),
      },
      ...(current ? [{
        key: "close-tab",
        label: "Close this tab",
        hint: frontKind === "terminal" ? "the terminal keeps running"
          : frontKind === "note" ? "the note stays in its folder" : undefined,
        icon: <Close size={15} />,
        words: "tab close",
        run: () => closeTab(current),
      }] : []),
      ...(runningHere ? [{
        key: "end-terminal",
        label: "End this terminal",
        hint: "its shell ends",
        icon: <Close size={15} />,
        words: "terminal end stop kill exit shell",
        run: () => askEnd(runningHere),
      }] : []),
      ...(noteHere && deletesNote(noteHere.project, noteHere.note, state.me.id) ? [{
        key: "delete-note",
        label: "Delete this note",
        hint: `from ${noteHere.folder.name}`,
        icon: <Trash size={15} />,
        words: "note delete remove",
        run: () => askDeleteNote(noteHere.note.id),
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
      { title: "Notes", items: notes, limit: 8 },
      { title: "New terminal", items: machineGroups().flatMap((g) => g.items) },
      { title: "Projects", items: pages },
      { title: "Actions", items: actions },
    ];
  };

  return (
    <div className={pinned ? "app pinned" : "app"}>
      <TabBar sidebarButton={sidebarButton} tabs={tabViews}
        current={current} onSelect={(tab) => navigate(tabPath(tab))}
        onClose={closeTab}
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
        }}
        saving={frontKind === "note" ? saveLine(saveStates[tabTarget(current!)]) : null} />

      <div className="stage">
        {shownTabs.filter(isTerminalTab).map((id) => (
          <TerminalView key={id} terminalId={id} active={id === current}
            shownTheme={shownTheme} onStatus={onStatus} onCloseTab={closeTab}
            onOpenNew={startTerminal} onChanged={refresh} notify={notYet}
            machineThere={(machineId) => machineIds.has(machineId)} />
        ))}
        {shownTabs.filter((tab) => tabKind(tab) === "note").map((tab) => (
          <NoteView key={tab} noteId={tabTarget(tab)} active={tab === current}
            me={state.me} projects={projects}
            fresh={fresh.current.has(tabTarget(tab))}
            onSummary={onSummary} onNote={onNote} onSaveState={onSaveState}
            onSettled={onSettled} onMoved={moved}
            onDelete={(noteId, noteTitle) => askDeleteNote(noteId, noteTitle)}
            onCloseTab={(tab, gone) => closeTabs([tab], gone)} notify={notYet} />
        ))}
        {frontKind === "project" && (
          <div className="page-scroll">
            {pageProject ? (
              <ProjectPage project={pageProject} me={state.me}
                terminals={liveTerminals} dotFor={dotFor}
                onAddMachine={startAdding} onOpenTerminal={startTerminal}
                onGoTo={goTo} onRemoveMachine={askRemove}
                onNewFolder={(project) => setNaming({ kind: "new", project })}
                onRenameFolder={(folder, project) =>
                  setNaming({ kind: "rename", folder, project })}
                onDeleteFolder={askDeleteFolder}
                onNewNote={newNote}
                onOpenNote={(noteId) => openTab(noteTab(noteId))}
                onMoveNote={moveFromList} onCopyNote={copyNote}
                onDeleteNote={(noteId) => askDeleteNote(noteId)}
                notYet={notYet} />
            ) : (
              <div className="status-page">
                <div className="status-card" role="status">
                  <h1>No such project</h1>
                  <p>It is not among your projects any more.</p>
                  <button type="button" className="button-plain"
                    onClick={() => closeTab(current!)}>
                    Close the tab
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
        {accountOpen && (
          <div className="page-scroll">
            <Account key={accountVisit} me={state.me} theme={theme}
              onTheme={chooseTheme}
              onRenamed={renamed} onSignOut={signOutHere}
              onSignedOut={(note) => {
                // This device forgotten: nothing of the person stays here.
                forgetDrafts();
                endSession(note);
              }} onClose={closeAccount}
              notify={notYet} />
          </div>
        )}
        {!current && !accountOpen && (
          <div className="page-scroll">
            {anyMachine ? (
              <Machines projects={projects} terminals={state.terminals}
                onAdd={(projectId) => startAdding(projectId)}
                onOpenTerminal={startTerminal} onGoTo={goTo}
                onRemove={askRemove} />
            ) : (
              <Welcome me={state.me} personal={personal}
                onAdd={(kind) => personal && startAdding(personal.id, kind)} />
            )}
          </div>
        )}
        {!online && shownTabs.some(isTerminalTab) && (
          <div className="lost-strip" role="status">
            Connection lost. Reconnecting; your terminals keep running.
          </div>
        )}
      </div>

      {sidebarShown && (
        <Sidebar
          me={state.me}
          projects={projects}
          pinned={pinned}
          shownTheme={shownTheme}
          onToggleTheme={toggleTheme}
          onTogglePin={togglePin}
          onClose={closeSidebar}
          onMouseEnter={cancelTimers}
          onMouseLeave={hoverAway}
          folded={folded}
          onFold={fold}
          current={current}
          terminals={state.terminals}
          dotFor={dotFor}
          onOpenProject={(projectId) => away(() => openTab(projectTab(projectId)))()}
          onAddMachine={(projectId) => startAdding(projectId)}
          onNewTerminal={(machine) => away(() => newTerminal(machine))()}
          onGoTo={(id) => away(() => goTo(id))()}
          onEndTerminal={askEnd}
          onNewFolder={(project) => setNaming({ kind: "new", project })}
          onNewNote={(folderId) => away(() => newNote(folderId))()}
          onOpenNote={(noteId) => away(() => openTab(noteTab(noteId)))()}
          onAccount={openAccount}
          accountOpen={accountOpen}
          onHome={away(() => navigate("/"))}
          notYet={notYet}
        />
      )}

      {palette && (
        <div className="palette-backdrop">
          <Finder className="palette" label="Jump to"
            placeholder="A terminal, a note, a machine or something to do"
            groups={paletteGroups()} onClose={() => setPalette(false)} />
        </div>
      )}

      {adding && (
        <AddMachine projects={projects} projectId={adding.projectId}
          kind={adding.kind} onClose={() => setAdding(null)}
          onConnected={refresh} notYet={notYet}
          onOpenTerminal={(machineId) => {
            setAdding(null);
            startTerminal(machineId);
          }} />
      )}

      {naming && (
        <FolderDialog
          title={naming.kind === "new" ? `New folder in ${naming.project.name}`
            : "Rename folder"}
          initial={naming.kind === "rename" ? naming.folder.name : ""}
          action={naming.kind === "new" ? "Create folder" : "Save"}
          help={naming.kind === "new"
            ? `${naming.project.personal ? "Only you see it."
              : `Everyone in ${naming.project.name} sees it and can write notes in it.`}` +
              " Then + next to the folder starts the first note."
            : naming.project.personal ? "Only you see it."
            : `Everyone in ${naming.project.name} sees the new name at once. ` +
              "The notes inside stay as they are."}
          onSave={(name) => saveFolder(naming, name)}
          onCancel={() => setNaming(null)} />
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
              tTerm Web stops signing in to it and takes its access back on
              the server itself. If the server does not answer, the command
              for that comes next.
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

      {asking?.kind === "note" && (
        <Confirm title={`Delete the note ${titled(asking.title)}?`}
          sub={`In ${asking.folder.name}.`} confirm="Delete note"
          onConfirm={() => removeNote(asking)}
          onCancel={() => setAsking(null)}>
          <p>
            {asking.project.personal ? "It is gone for good."
              : `Everyone in ${asking.project.name} loses it, also whoever has ` +
                "it open now. The journal keeps a line: who deleted it, and when."}
          </p>
        </Confirm>
      )}

      {asking?.kind === "folder" && (() => {
        const inside = asking.folder.notes;
        const titles = inside.map((n) => titled(n.title));
        const named = titles.length > 5
          ? listed([...titles.slice(0, 4), `${titles.length - 4} more`])
          : listed(titles);
        return (
          <Confirm title={`Delete the folder ${asking.folder.name}?`}
            confirm={inside.length
              ? `Delete folder and ${many(inside.length, "note", "notes")}`
              : "Delete folder"}
            onConfirm={() => removeFolder(asking)}
            onCancel={() => setAsking(null)}>
            <p className="ink">
              {inside.length
                ? `Its ${inside.length === 1 ? "note goes" : `${inside.length} notes go`} ` +
                  `with it: ${named}.`
                : "The folder is empty."}
            </p>
            {inside.length > 0 && !asking.project.personal && (
              <p>
                Everyone in {asking.project.name} loses them, also whoever has
                one open now. The journal keeps a line: who deleted the folder,
                and when.
              </p>
            )}
            {inside.length > 0 && (
              <p className="keep-note">
                <FolderIcon size={15} />
                <span>To keep a note, move it first: ⋯ next to the note, then
                  another folder.</span>
              </p>
            )}
          </Confirm>
        );
      })()}

      {removed && (
        <Removed name={removed.name} cleanup={removed.cleanup}
          onClose={() => setRemoved(null)} notify={notYet} />
      )}

      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
