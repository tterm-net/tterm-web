// The sliding sidebar: one menu for everything, as approved on 11.10, with
// notes beside machines from 0.1.0, as on the canvas of 19.10. Each project
// has its machines with their terminals, and its notes in folders. A machine
// and a folder are the same kind of row: a click folds or unfolds it, and
// its "+" opens a new terminal on the machine or starts a note in the
// folder. The project's name opens its page. What is folded is remembered
// in this browser.
//
// It slides over the page and does not push it, so that opening it never
// changes the terminal's width; pinned, it narrows the page once. A terminal
// pointed at offers End (from 0.0.8), which asks first. The account at the
// bottom opens the account page (from 0.0.10). From 0.1.1 each project's
// badge is in its color, and New project makes one of one's own. From 0.1.2
// a double click on a terminal names it right there.

import type { Machine, Me, Project, Role, TerminalInfo } from "./api";
import { colorClass, terminalName } from "./colors";
import {
  ChevronDown, Close, Computer, Folder as FolderIcon, FolderOpen, Logo, Moon,
  Pin, Plus, Server, Sun,
} from "./icons";
import { NameInput } from "./NameInput";
import { Avatar, NoteIcon } from "./ProjectPage";
import { titled } from "./noteText";
import { manages, writes } from "./rights";
import type { Dot } from "./TabBar";
import { noteTab, projectTab } from "./tabs";
import type { Shown } from "./theme";
import { lastSeen } from "./time";

const ROLE_LABEL: Record<Role, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  observer: "Observer",
};

interface Props {
  me: Me;
  projects: Project[];
  pinned: boolean;
  shownTheme: Shown;
  onToggleTheme: () => void;
  onTogglePin: () => void;
  onClose: () => void;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  /** What is folded: p:, m: and f: with the id of a project, a machine or a
   * folder. */
  folded: Set<string>;
  onFold: (key: string) => void;
  /** The tab in front. */
  current: string | null;
  terminals: TerminalInfo[];
  dotFor: (terminal: TerminalInfo) => Dot;
  onOpenProject: (projectId: string) => void;
  onAddMachine: (projectId: string) => void;
  onNewTerminal: (machine: Machine) => void;
  onGoTo: (terminalId: string) => void;
  onEndTerminal: (terminal: TerminalInfo) => void;
  /** The terminal being named here, if one is (0.1.2). */
  renaming: string | null;
  onStartRename: (terminalId: string) => void;
  onRename: (terminalId: string, name: string) => void;
  onStopRename: (terminalId: string) => void;
  /** The second press of a double click: the sidebar stays, a click on a
   * terminal having asked it to slide away. */
  onKeepOpen: () => void;
  onNewFolder: (project: Project) => void;
  onNewNote: (folderId: string) => void;
  onOpenNote: (noteId: string) => void;
  onNewProject: () => void;
  /** The machines page. */
  onHome: () => void;
  notYet: (what: string) => void;
  /** The account page, and whether it is the one shown. */
  onAccount: () => void;
  accountOpen: boolean;
}

/** The ⌄ before a row: turned while folded. Only for the mouse: the row's
 * name does the same and is the one the keyboard reaches. */
function Chevron({ open, onClick }: { open: boolean; onClick: () => void }) {
  return (
    <button type="button" className="sb-chevron" tabIndex={-1} aria-hidden="true"
      data-open={open} onClick={onClick}>
      <ChevronDown size={13} />
    </button>
  );
}

function Label(props: { text: string; plus?: { title: string; onClick: () => void } }) {
  return (
    <div className="sb-label">
      <span>{props.text}</span>
      {props.plus && (
        <button type="button" className="sb-plus" aria-label={props.plus.title}
          title={props.plus.title} onClick={props.plus.onClick}>
          <Plus size={13} />
        </button>
      )}
    </div>
  );
}

export function Sidebar(props: Props) {
  const { me, projects, pinned, shownTheme, folded, onFold, current } = props;
  const { terminals, dotFor } = props;
  const themeLabel =
    shownTheme === "light" ? "Switch to dark theme" : "Switch to light theme";

  const machineRows = (m: Machine) => {
    const key = `m:${m.id}`;
    const open = !folded.has(key);
    const mine = terminals.filter((t) => t.machineId === m.id);
    const computer = m.kind === "computer";
    const offline = computer && !m.online;
    const seen = offline ? lastSeen(m.lastSeen) : "";
    return (
      <div key={m.id} className="sb-group">
        <div className="sb-row">
          <Chevron open={open} onClick={() => onFold(key)} />
          <button type="button" className={offline ? "sb-name offline" : "sb-name"}
            aria-expanded={open}
            title={offline
              ? `${m.name} is offline: asleep, or its agent is not running${seen ? ` (${seen})` : ""}`
              : `${open ? "Fold" : "Unfold"} ${m.name}`}
            onClick={() => onFold(key)}>
            {computer ? <Computer size={14} /> : <Server size={14} />}
            <span className="sb-text">{m.name}</span>
            {!open && mine.length > 0 && <span className="sb-count">{mine.length}</span>}
            {computer && (
              <span className={offline ? "sb-state away" : "sb-state on"}
                aria-hidden="true" />
            )}
          </button>
          <button type="button" className="sb-plus"
            aria-label={`New terminal on ${m.name}`} title="New terminal"
            onClick={() => props.onNewTerminal(m)}>
            <Plus size={13} />
          </button>
        </div>
        {open && mine.map((t) => {
          const dot = dotFor(t);
          const canRename = dot !== "ended";
          if (canRename && props.renaming === t.id) {
            return (
              <div key={t.id} className="sb-rename">
                <div className="sb-rename-row">
                  <Avatar who={null} me={me} size="xs" />
                  <NameInput id={t.id} name={t.name} className="sb-rename-input"
                    describedBy={`sb-rename-${t.id}`} onRename={props.onRename}
                    onStop={() => props.onStopRename(t.id)} />
                </div>
                <span className="sb-rename-hint" id={`sb-rename-${t.id}`}>
                  Enter saves, Esc cancels
                </span>
              </div>
            );
          }
          const said = dot === "away"
            ? `Go to ${t.label}: it waits for ${m.name} to come back`
            : `Go to ${t.label}`;
          return (
            <div key={t.id} className="sb-leaf-row">
              <button type="button" className="sb-leaf"
                aria-current={current === t.id ? "page" : undefined}
                title={canRename ? `${said}\nDouble-click to rename` : said}
                onMouseDown={(e) => {
                  if (e.detail > 1 && canRename) props.onKeepOpen();
                }}
                onClick={(e) => {
                  // The second click of a double one: the name, not again
                  // the terminal.
                  if (e.detail < 2) props.onGoTo(t.id);
                }}
                onDoubleClick={() => {
                  if (canRename) props.onStartRename(t.id);
                }}>
                <Avatar who={null} me={me} size="xs" />
                <span className="sb-text">{terminalName(t.name)}</span>
                {dot !== "open" && <span className={`dot ${dot}`} aria-hidden="true" />}
              </button>
              <button type="button" className="tree-action"
                aria-label={`End ${t.label}`} title={`End ${t.label}: its shell ends`}
                onClick={() => props.onEndTerminal(t)}>
                <Close size={13} />
              </button>
            </div>
          );
        })}
      </div>
    );
  };

  const folderRows = (project: Project) => project.folders.map((f) => {
    const key = `f:${f.id}`;
    const open = !folded.has(key);
    return (
      <div key={f.id} className="sb-group">
        <div className="sb-row">
          <Chevron open={open} onClick={() => onFold(key)} />
          <button type="button" className="sb-name" aria-expanded={open}
            title={`${open ? "Fold" : "Unfold"} ${f.name}`}
            onClick={() => onFold(key)}>
            {open ? <FolderOpen size={14} /> : <FolderIcon size={14} />}
            <span className="sb-text">{f.name}</span>
            {!open && f.notes.length > 0 && <span className="sb-count">{f.notes.length}</span>}
          </button>
          {writes(project) && (
            <button type="button" className="sb-plus"
              aria-label={`New note in ${f.name}`} title={`New note in ${f.name}`}
              onClick={() => props.onNewNote(f.id)}>
              <Plus size={13} />
            </button>
          )}
        </div>
        {open && f.notes.map((n) => (
          <button key={n.id} type="button" className="sb-leaf"
            aria-current={current === noteTab(n.id) ? "page" : undefined}
            onClick={() => props.onOpenNote(n.id)}>
            <span className="sb-leaf-icon" aria-hidden="true"><NoteIcon note={n} /></span>
            <span className={n.title.trim() ? "sb-text" : "sb-text untitled"}>
              {titled(n.title)}
            </span>
            {n.total > 0 && <span className="sb-progress">{n.done}/{n.total}</span>}
          </button>
        ))}
        {open && f.notes.length === 0 && (
          <div className="sb-none deep">
            {writes(project) ? "Empty. + starts a note" : "Empty"}
          </div>
        )}
      </div>
    );
  });

  return (
    <aside className="sidebar" aria-label="Sidebar"
      onMouseEnter={props.onMouseEnter} onMouseLeave={props.onMouseLeave}>
      <div className="sidebar-head">
        <button type="button" className="brand-button" onClick={props.onHome}
          title="All machines">
          <Logo size={22} />
          <span className="brand">tTerm</span>
        </button>
        <button type="button" className="icon-button small"
          aria-label="Keep sidebar open" aria-pressed={pinned}
          title={pinned ? "Let the sidebar slide away" : "Keep the sidebar open"}
          onClick={props.onTogglePin}>
          <Pin size={16} />
        </button>
        <button type="button" className="icon-button small"
          aria-label="Close sidebar" onClick={props.onClose}>
          <Close size={16} />
        </button>
      </div>

      <div className="sidebar-body">
        <div className="section-label">Projects</div>
        {projects.map((project) => {
          const key = `p:${project.id}`;
          const open = !folded.has(key);
          return (
            <div key={project.id} className={`sb-project ${colorClass(project.color)}`}>
              <div className="project-row">
                <button type="button" className="chevron" aria-expanded={open}
                  aria-label={`${open ? "Fold" : "Unfold"} ${project.name}`}
                  onClick={() => onFold(key)}>
                  <ChevronDown size={14} />
                </button>
                <button type="button" className="project-name"
                  aria-current={current === projectTab(project.id) ? "page" : undefined}
                  title={`Open ${project.name}: its machines and notes`}
                  onClick={() => props.onOpenProject(project.id)}>
                  <span className="code-badge" aria-hidden="true">{project.code}</span>
                  <span className="project-title">{project.name}</span>
                  <span className="role">{ROLE_LABEL[project.role]}</span>
                </button>
              </div>
              {open && (
                <div className="sb-inside">
                  <Label text="Machines" plus={manages(project) ? {
                    title: "Add machine",
                    onClick: () => props.onAddMachine(project.id),
                  } : undefined} />
                  {project.machines.length === 0 && (
                    <div className="sb-none">No machines yet</div>
                  )}
                  {project.machines.map(machineRows)}
                  <Label text="Notes" plus={writes(project) ? {
                    title: "New folder",
                    onClick: () => props.onNewFolder(project),
                  } : undefined} />
                  {project.folders.length === 0 && (
                    <div className="sb-none">
                      {writes(project) ? "No notes yet. + makes a folder for them"
                        : "No notes yet"}
                    </div>
                  )}
                  {folderRows(project)}
                </div>
              )}
            </div>
          );
        })}
        <button type="button" className="new-project" onClick={props.onNewProject}>
          <span className="dashed-tile" aria-hidden="true">
            <Plus size={13} />
          </span>
          New project
        </button>
      </div>

      <div className="sidebar-foot">
        <AccountButton me={me} onAccount={props.onAccount}
          current={props.accountOpen} />
        <button type="button" className="icon-button"
          aria-label={themeLabel} title={themeLabel}
          onClick={props.onToggleTheme}>
          {shownTheme === "light" ? <Moon size={17} /> : <Sun size={17} />}
        </button>
      </div>
    </aside>
  );
}

/** The account at the bottom of the sidebar: the name and the plan. It opens
 * the account page, and lights up under the mouse like anything else here
 * that does something, so it needs no word saying so. */
function AccountButton(props: { me: Me; onAccount: () => void; current: boolean }) {
  const { me } = props;
  const plan = me.plan === "free" ? "Free plan" : me.plan;
  return (
    <div className="account-box">
      <button type="button" className="account" onClick={props.onAccount}
        aria-current={props.current ? "page" : undefined}
        title="Your account and settings">
        <span className="avatar mine" aria-hidden="true">{me.initials}</span>
        <span className="account-text">
          <strong>{me.firstName}</strong>
          <span>{plan}</span>
        </span>
      </button>
    </div>
  );
}
