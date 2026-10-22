// A project's page, in a tab of its own, opened from the project's name in
// the sidebar (0.1.0): its machines with the terminals running on them, and
// its notes in folders, as on the approved canvas. From 0.1.1 the pencil
// next to its name changes the name, the code and the color, ⋯ next to a
// machine moves it to another project of one's own, and Delete project is
// at the very end. From 0.1.2 a double click on a terminal's name names it,
// and a note's ⋯ moves it to another project. People and the journal come
// with teams, later. From 0.1.4, in the app before signing in (local.ts),
// its computer has no menu; from 0.1.5 Add machine and the card about
// Personal, with New project, are there as after signing in, and say what
// signing in brings (NeedAccount.tsx).

import { useEffect, useState } from "react";
import type {
  Folder, Machine, MachineKind, Me, NoteSummary, Project, TerminalInfo, Who,
} from "./api";
import { shortPath } from "./BlockView";
import { colorClass, terminalName } from "./colors";
import {
  Computer, Doc, Folder as FolderIcon, FolderPlus, ListCheck, Lock, Pencil,
  Plus, Server, Trash,
} from "./icons";
import { FolderMenuItems, MenuButton, NoteMenuItems } from "./Menus";
import { NameInput } from "./NameInput";
import { titled } from "./noteText";
import {
  deletesFolder, deletesNote, many, manages, writes,
} from "./rights";
import type { Dot } from "./TabBar";
import { ago, lastSeen, since } from "./time";

const ROLE_LINE: Record<Project["role"], string> = {
  owner: "you are the owner",
  admin: "you are an admin",
  member: "you are a member",
  observer: "you are an observer",
};

interface Props {
  project: Project;
  me: Me;
  /** The app before signing in: this computer only (0.1.4). */
  local?: boolean;
  /** The terminals running now, in every project. */
  terminals: TerminalInfo[];
  dotFor: (terminal: TerminalInfo) => Dot;
  onAddMachine: (projectId: string, kind?: MachineKind) => void;
  onOpenTerminal: (machineId: string) => void;
  onGoTo: (terminalId: string) => void;
  onRemoveMachine: (machine: Machine) => void;
  /** Moves a machine to another project of one's own: only when there is
   * another one. */
  onMoveMachine?: (machine: Machine) => void;
  onEditProject: (project: Project) => void;
  onDeleteProject: (project: Project) => void;
  onNewProject: () => void;
  onNewFolder: (project: Project) => void;
  onRenameFolder: (folder: Folder, project: Project) => void;
  onDeleteFolder: (folder: Folder, project: Project) => void;
  onNewNote: (folderId: string) => void;
  onOpenNote: (noteId: string) => void;
  onMoveNote: (noteId: string, folderId: string) => void;
  /** To another project (0.1.2): only when there is one to write in. */
  onMoveNoteOut?: (noteId: string) => void;
  onCopyNote: (noteId: string) => void;
  onDeleteNote: (noteId: string) => void;
  /** The terminal whose name is being typed here, if one is (0.1.2). */
  renaming: string | null;
  onStartRename: (terminalId: string) => void;
  onRename: (terminalId: string, name: string) => void;
  onStopRename: (terminalId: string) => void;
  notYet: (what: string) => void;
}

/** Naming a terminal on the page (0.1.2), as in its tab. */
interface Naming {
  renaming: string | null;
  onStartRename: (terminalId: string) => void;
  onRename: (terminalId: string, name: string) => void;
  onStopRename: (terminalId: string) => void;
}

/** A person's initials in a circle, amber for oneself: 26, 22 or 18 pixels
 * across, or the account's 30. */
export function Avatar({ who, me, size }: {
  who: Who | null;
  me: Me;
  size?: "small" | "tiny" | "xs";
}) {
  const mine = !who || who.id === me.id;
  return (
    <span className={`avatar${size ? ` ${size}` : ""}${mine ? " mine" : ""}`}
      aria-hidden="true">
      {who?.initials ?? me.initials}
    </span>
  );
}

export function NoteIcon({ note, size = 14 }: { note: NoteSummary; size?: number }) {
  return note.total > 0 ? <ListCheck size={size} /> : <Doc size={size} />;
}

function capital(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

function TerminalRow({ t, me, dot, onGoTo, naming }: {
  t: TerminalInfo;
  me: Me;
  dot: Dot;
  onGoTo: (id: string) => void;
  naming: Naming;
}) {
  const canRename = dot !== "ended";
  const where = t.cwd ? shortPath(t.cwd, t.home) : "";
  let text = [where, t.branch].filter(Boolean).join(" · ");
  let when = "";
  if (dot === "busy") {
    text = t.running?.command || "A command is running";
    when = t.running?.at ? `running for ${since(t.running.at)}` : "running";
  } else if (dot === "away") {
    when = `waits for ${t.machine}`;
  }
  return (
    <li className="pp-row">
      <Avatar who={null} me={me} size="small" />
      <span className="pp-row-text">
        {canRename && naming.renaming === t.id ? (
          <>
            <NameInput id={t.id} name={t.name} className="pp-rename-input"
              describedBy={`pp-rename-${t.id}`} onRename={naming.onRename}
              onStop={() => naming.onStopRename(t.id)} />
            <span className="pp-rename-hint" id={`pp-rename-${t.id}`}>
              Enter saves, Esc cancels.
            </span>
          </>
        ) : (
          <>
            <strong className={canRename ? "pp-term-name" : undefined}
              title={canRename ? "Double-click to rename" : undefined}
              onDoubleClick={() => {
                if (canRename) naming.onStartRename(t.id);
              }}>
              {terminalName(t.name)}
            </strong>
            <span className="pp-row-sub mono">
              <span className={`dot ${dot}`} aria-hidden="true" />
              <span className="pp-ellipsis">{text || "~"}</span>
              {when && <span className={dot === "busy" ? "pp-when busy" : "pp-when"}>{when}</span>}
            </span>
          </>
        )}
      </span>
      <span className="pp-row-actions">
        <button type="button" className="button-plain small" onClick={() => onGoTo(t.id)}>
          Open
        </button>
      </span>
    </li>
  );
}

function MachineCard(props: {
  machine: Machine;
  project: Project;
  me: Me;
  terminals: TerminalInfo[];
  dotFor: (terminal: TerminalInfo) => Dot;
  onOpenTerminal: (machineId: string) => void;
  onGoTo: (terminalId: string) => void;
  onRemoveMachine: (machine: Machine) => void;
  onMoveMachine?: (machine: Machine) => void;
  naming: Naming;
  notYet: (what: string) => void;
  /** The app before signing in: the computer it runs on stays. */
  local?: boolean;
}) {
  const { machine: m, project } = props;
  const computer = m.kind === "computer";
  const offline = computer && !m.online;
  const seen = offline ? lastSeen(m.lastSeen) : "";
  return (
    <div className="pp-card">
      <div className="pp-card-head">
        <span className="pp-card-name">
          {computer ? <Computer size={16} /> : <Server size={16} />}
          {m.name}
        </span>
        {m.host && <span className="pp-mono">{m.host}</span>}
        {m.os && <span className="pp-dim">{m.os}</span>}
        {computer && (
          <span className="pp-dim pp-state">
            <span className={offline ? "dot away" : "dot open"} aria-hidden="true" />
            {offline ? capital(seen) || "Offline" : "Online"}
          </span>
        )}
        <span className="pp-card-actions">
          <button type="button" className="button-ghost small"
            title={offline ? `${m.name} is offline: asleep, or its agent is not running`
              : `A new terminal on ${m.name}`}
            onClick={() => (offline
              ? props.notYet(`${m.name} is offline${seen ? `, ${seen}` : ""}: it comes back when its agent connects.`)
              : props.onOpenTerminal(m.id))}>
            <Plus size={14} />
            New terminal
          </button>
          {manages(project) && !props.local && (
            <MenuButton label={`${m.name}: machine menu`}
              title={props.onMoveMachine ? "Move to another project, Remove machine"
                : "Remove machine"}>
              {(close) => (
                <>
                  {props.onMoveMachine && (
                    <button type="button" className="menu-item" role="menuitem"
                      onClick={() => {
                        close();
                        props.onMoveMachine?.(m);
                      }}>
                      <FolderIcon size={15} />
                      <span className="menu-text">Move to another project</span>
                    </button>
                  )}
                  <button type="button" className="menu-item danger" role="menuitem"
                    onClick={() => {
                      close();
                      props.onRemoveMachine(m);
                    }}>
                    <Trash size={15} />
                    <span className="menu-text">Remove machine</span>
                  </button>
                </>
              )}
            </MenuButton>
          )}
        </span>
      </div>
      <ul className="pp-rows">
        {props.terminals.map((t) => (
          <TerminalRow key={t.id} t={t} me={props.me} dot={props.dotFor(t)}
            onGoTo={props.onGoTo} naming={props.naming} />
        ))}
        {props.terminals.length === 0 && <li className="pp-empty">No terminals open</li>}
      </ul>
    </div>
  );
}

function NoteRow(props: {
  note: NoteSummary;
  folder: Folder;
  project: Project;
  me: Me;
  onOpenNote: (noteId: string) => void;
  onMoveNote: (noteId: string, folderId: string) => void;
  onMoveNoteOut?: (noteId: string) => void;
  onCopyNote: (noteId: string) => void;
  onDeleteNote: (noteId: string) => void;
}) {
  const { note: n, me, project } = props;
  const canDelete = deletesNote(project, n, me.id);
  const moveOut = !!props.onMoveNoteOut && canDelete;
  const editor = n.editedBy ?? n.createdBy;
  const name = !editor || editor.id === me.id ? "You" : editor.firstName;
  const author = !n.createdBy || n.createdBy.id === me.id ? "You" : n.createdBy.firstName;
  const sub = n.total ? `${n.done} of ${n.total} done` : n.excerpt || "Empty";
  return (
    <li className="pp-note-row">
      <button type="button" className="pp-note" onClick={() => props.onOpenNote(n.id)}>
        <span className="pp-note-tile" aria-hidden="true"><NoteIcon note={n} /></span>
        <span className="pp-row-text">
          <strong className={n.title.trim() ? undefined : "untitled"}>{titled(n.title)}</strong>
          <span className="pp-row-sub">
            {n.total > 0 && (
              <span className="pp-progress" aria-hidden="true">
                <span style={{ width: `${Math.round((100 * n.done) / n.total)}%` }} />
              </span>
            )}
            <span className="pp-ellipsis">{sub}</span>
          </span>
        </span>
        <span className="pp-who">
          <Avatar who={editor} me={me} size="tiny" />
          {name}, {ago(n.editedAt)}
        </span>
      </button>
      <MenuButton label={`${titled(n.title)}: note menu`}
        title={moveOut ? "Folder, Move to another project, Copy as Markdown, Delete note"
          : "Folder, Copy as Markdown, Delete note"}>
        {(close) => (
          <NoteMenuItems folders={project.folders} folderId={props.folder.id}
            canWrite={writes(project)} canDelete={canDelete}
            elsewhere={!!props.onMoveNoteOut} author={author} close={close}
            onMove={(folderId) => props.onMoveNote(n.id, folderId)}
            onMoveOut={() => props.onMoveNoteOut?.(n.id)}
            onCopy={() => props.onCopyNote(n.id)}
            onDelete={() => props.onDeleteNote(n.id)} />
        )}
      </MenuButton>
    </li>
  );
}

export function ProjectPage(props: Props) {
  const { project, me } = props;
  const [, setTick] = useState(0);

  // "5 min ago" and "running for 3 minutes" go on counting.
  useEffect(() => {
    const every = window.setInterval(() => setTick((n) => n + 1), 30_000);
    return () => window.clearInterval(every);
  }, []);

  const machineIds = new Set(project.machines.map((m) => m.id));
  const running = props.terminals.filter((t) => machineIds.has(t.machineId));
  // Nobody else in it: Personal, and until people come, every project.
  const alone = project.personal || project.alone;
  const line = [
    many(project.machines.length, "machine", "machines"),
    many(running.length, "terminal open", "terminals open"),
    alone ? "only you" : ROLE_LINE[project.role],
  ].join(" · ");
  const manage = manages(project);
  // Before signing in too: App says what signing in brings (0.1.5).
  const addsMachines = manage;
  const write = writes(project);
  const naming: Naming = {
    renaming: props.renaming, onStartRename: props.onStartRename,
    onRename: props.onRename, onStopRename: props.onStopRename,
  };

  return (
    <main className="project-view" aria-labelledby="pp-title">
      <div className="pp-head">
        <div className={`pp-title-row ${colorClass(project.color)}`}>
          <h1 id="pp-title">{project.name}</h1>
          <span className="pp-code" title="Short code">{project.code}</span>
          {manage && (
            <button type="button" className="icon-button small"
              aria-label="Edit name, code and color"
              title="Edit name, code and color"
              onClick={() => props.onEditProject(project)}>
              <Pencil size={16} />
            </button>
          )}
        </div>
        <p>{line}</p>
      </div>

      <section className="pp-section" aria-labelledby="pp-machines">
        <div className="pp-section-head">
          <div className="pp-section-title">
            <h2 id="pp-machines">Machines</h2>
            <span>{alone ? "Only you open terminals on them"
              : "Your terminals on them"}</span>
          </div>
          {addsMachines && (
            <button type="button" className="button-plain small"
              onClick={() => props.onAddMachine(project.id)}>
              <Plus size={14} />
              Add machine
            </button>
          )}
        </div>
        {project.machines.length === 0 ? (
          <div className="pp-kinds">
            <button type="button" className="pp-kind" disabled={!addsMachines}
              onClick={() => props.onAddMachine(project.id, "server")}>
              <span className="tile" aria-hidden="true"><Server size={20} /></span>
              <span className="pp-kind-text">
                <strong>Server</strong>
                <span>A VPS or cloud server you reach over SSH. Run one command
                  on it, and it appears here.</span>
              </span>
            </button>
            <button type="button" className="pp-kind" disabled={!addsMachines}
              onClick={() => props.onAddMachine(project.id, "computer")}>
              <span className="tile" aria-hidden="true"><Computer size={20} /></span>
              <span className="pp-kind-text">
                <strong>Computer</strong>
                <span>A Mac or Linux machine at home or in the office, even
                  behind NAT. A small agent keeps it connected.</span>
              </span>
            </button>
          </div>
        ) : (
          <div className="pp-cards">
            {project.machines.map((m) => (
              <MachineCard key={m.id} machine={m} project={project} me={me}
                terminals={running.filter((t) => t.machineId === m.id)}
                dotFor={props.dotFor} onOpenTerminal={props.onOpenTerminal}
                onGoTo={props.onGoTo} onRemoveMachine={props.onRemoveMachine}
                onMoveMachine={props.onMoveMachine} naming={naming}
                notYet={props.notYet} local={props.local} />
            ))}
          </div>
        )}
        {project.machines.length === 0 && !project.personal && addsMachines && (
          <p className="pp-hint">
            Already have the machine in another project? On that project's page,
            ⋯ next to the machine, then Move to another project.
          </p>
        )}
      </section>

      {project.personal && (
        <div className="pp-wide">
          <span className="tile" aria-hidden="true"><Lock size={20} /></span>
          <span className="pp-wide-text">
            <strong>{project.name} is yours alone</strong>
            <span>No one can be invited here. To keep other machines apart, or
              later to work with others, create a project and move machines
              into it.</span>
          </span>
          <button type="button" className="button-plain" onClick={props.onNewProject}>
            New project
          </button>
        </div>
      )}

      <section className="pp-section" aria-labelledby="pp-notes">
        <div className="pp-section-head">
          <div className="pp-section-title">
            <h2 id="pp-notes">Notes</h2>
            <span>{alone
              ? `Only you see notes in ${project.name}. Folders keep them in order`
              : `Everyone in ${project.name} reads and edits them. Folders keep them in order`}</span>
          </div>
          {write && (
            <button type="button" className="button-plain small"
              onClick={() => props.onNewFolder(project)}>
              <FolderPlus size={14} />
              New folder
            </button>
          )}
        </div>
        {project.folders.length === 0 ? (
          <div className="pp-wide">
            <span className="tile" aria-hidden="true"><FolderIcon size={20} /></span>
            <span className="pp-wide-text">
              <strong>No notes yet</strong>
              <span>Notes live in folders, the way terminals live on machines.
                Start with one: Runbooks, Access, Checklists.</span>
            </span>
            {write && (
              <button type="button" className="button-accent big"
                onClick={() => props.onNewFolder(project)}>
                <FolderPlus size={15} />
                New folder
              </button>
            )}
          </div>
        ) : (
          <div className="pp-cards">
            {project.folders.map((f) => (
              <div key={f.id} className="pp-card">
                <div className="pp-card-head">
                  <span className="pp-card-name">
                    <FolderIcon size={16} />
                    {f.name}
                  </span>
                  <span className="pp-dim">
                    {f.notes.length ? many(f.notes.length, "note", "notes") : "Empty"}
                  </span>
                  {!alone && f.createdBy && (
                    <span className="pp-faint">
                      made by {f.createdBy.id === me.id ? "you" : f.createdBy.firstName}
                    </span>
                  )}
                  <span className="pp-card-actions">
                    {write && (
                      <button type="button" className="button-ghost small"
                        onClick={() => props.onNewNote(f.id)}>
                        <Plus size={14} />
                        New note
                      </button>
                    )}
                    {write && (
                      <MenuButton label={`${f.name}: folder menu`}
                        title="Rename folder, Delete folder">
                        {(close) => (
                          <FolderMenuItems canWrite={write}
                            canDelete={deletesFolder(project, f, me.id)} close={close}
                            onRename={() => props.onRenameFolder(f, project)}
                            onDelete={() => props.onDeleteFolder(f, project)} />
                        )}
                      </MenuButton>
                    )}
                  </span>
                </div>
                <ul className="pp-rows">
                  {f.notes.map((n) => (
                    <NoteRow key={n.id} note={n} folder={f} project={project} me={me}
                      onOpenNote={props.onOpenNote} onMoveNote={props.onMoveNote}
                      onMoveNoteOut={props.onMoveNoteOut}
                      onCopyNote={props.onCopyNote} onDeleteNote={props.onDeleteNote} />
                  ))}
                  {f.notes.length === 0 && (
                    <li className="pp-empty">
                      {write ? "No notes yet. New note puts the first one here"
                        : "No notes yet"}
                    </li>
                  )}
                </ul>
              </div>
            ))}
          </div>
        )}
      </section>

      {project.own && !project.personal && (
        <section className="pp-section" aria-labelledby="pp-delete">
          <div className="pp-wide pp-danger">
            <span className="pp-wide-text">
              <strong id="pp-delete">Delete {project.name}</strong>
              <span>Its folders and notes go with it. Machines are moved to
                another project or removed first.</span>
            </span>
            <button type="button" className="button-danger-line"
              onClick={() => props.onDeleteProject(project)}>
              Delete project
            </button>
          </div>
        </section>
      )}
    </main>
  );
}
