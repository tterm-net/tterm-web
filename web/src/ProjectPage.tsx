// A project's page, in a tab of its own, opened from the project's name in
// the sidebar (0.1.0): its machines with the terminals running on them, and
// its notes in folders, as on the approved canvas. People and the journal
// come with teams, in the next step.

import { useEffect, useState } from "react";
import type {
  Folder, Machine, MachineKind, Me, NoteSummary, Project, TerminalInfo, Who,
} from "./api";
import { shortPath } from "./BlockView";
import {
  Computer, Doc, Folder as FolderIcon, FolderPlus, ListCheck, Lock, Plus,
  Server, Trash,
} from "./icons";
import { FolderMenuItems, MenuButton, NoteMenuItems } from "./Menus";
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
  /** The terminals running now, in every project. */
  terminals: TerminalInfo[];
  dotFor: (terminal: TerminalInfo) => Dot;
  onAddMachine: (projectId: string, kind?: MachineKind) => void;
  onOpenTerminal: (machineId: string) => void;
  onGoTo: (terminalId: string) => void;
  onRemoveMachine: (machine: Machine) => void;
  onNewFolder: (project: Project) => void;
  onRenameFolder: (folder: Folder, project: Project) => void;
  onDeleteFolder: (folder: Folder, project: Project) => void;
  onNewNote: (folderId: string) => void;
  onOpenNote: (noteId: string) => void;
  onMoveNote: (noteId: string, folderId: string) => void;
  onCopyNote: (noteId: string) => void;
  onDeleteNote: (noteId: string) => void;
  notYet: (what: string) => void;
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

function TerminalRow({ t, me, dot, onGoTo }: {
  t: TerminalInfo;
  me: Me;
  dot: Dot;
  onGoTo: (id: string) => void;
}) {
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
        <strong>terminal {t.name}</strong>
        <span className="pp-row-sub mono">
          <span className={`dot ${dot}`} aria-hidden="true" />
          <span className="pp-ellipsis">{text || "~"}</span>
          {when && <span className={dot === "busy" ? "pp-when busy" : "pp-when"}>{when}</span>}
        </span>
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
  notYet: (what: string) => void;
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
          {manages(project) && (
            <MenuButton label={`${m.name}: machine menu`} title="Remove machine">
              {(close) => (
                <button type="button" className="menu-item danger" role="menuitem"
                  onClick={() => {
                    close();
                    props.onRemoveMachine(m);
                  }}>
                  <Trash size={15} />
                  <span className="menu-text">Remove machine</span>
                </button>
              )}
            </MenuButton>
          )}
        </span>
      </div>
      <ul className="pp-rows">
        {props.terminals.map((t) => (
          <TerminalRow key={t.id} t={t} me={props.me} dot={props.dotFor(t)}
            onGoTo={props.onGoTo} />
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
  onCopyNote: (noteId: string) => void;
  onDeleteNote: (noteId: string) => void;
}) {
  const { note: n, me, project } = props;
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
        title="Folder, Copy as Markdown, Delete note">
        {(close) => (
          <NoteMenuItems folders={project.folders} folderId={props.folder.id}
            canWrite={writes(project)} canDelete={deletesNote(project, n, me.id)}
            author={author} close={close}
            onMove={(folderId) => props.onMoveNote(n.id, folderId)}
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
  const line = [
    many(project.machines.length, "machine", "machines"),
    many(running.length, "terminal open", "terminals open"),
    project.personal ? "only you" : ROLE_LINE[project.role],
  ].join(" · ");
  const manage = manages(project);
  const write = writes(project);

  return (
    <main className="project-view" aria-labelledby="pp-title">
      <div className="pp-head">
        <div className="pp-title-row">
          <h1 id="pp-title">{project.name}</h1>
          <span className="pp-code" title="Short code">{project.code}</span>
        </div>
        <p>{line}</p>
      </div>

      <section className="pp-section" aria-labelledby="pp-machines">
        <div className="pp-section-head">
          <div className="pp-section-title">
            <h2 id="pp-machines">Machines</h2>
            <span>{project.personal ? "Only you open terminals on them"
              : "Your terminals on them"}</span>
          </div>
          {manage && (
            <button type="button" className="button-plain small"
              onClick={() => props.onAddMachine(project.id)}>
              <Plus size={14} />
              Add machine
            </button>
          )}
        </div>
        {project.machines.length === 0 ? (
          <div className="pp-kinds">
            <button type="button" className="pp-kind" disabled={!manage}
              onClick={() => props.onAddMachine(project.id, "server")}>
              <span className="tile" aria-hidden="true"><Server size={20} /></span>
              <span className="pp-kind-text">
                <strong>Server</strong>
                <span>A VPS or cloud server you reach over SSH. Run one command
                  on it, and it appears here.</span>
              </span>
            </button>
            <button type="button" className="pp-kind" disabled={!manage}
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
                notYet={props.notYet} />
            ))}
          </div>
        )}
      </section>

      {project.personal && (
        <div className="pp-wide">
          <span className="tile" aria-hidden="true"><Lock size={20} /></span>
          <span className="pp-wide-text">
            <strong>Personal is yours alone</strong>
            <span>No one can be invited here. To work with others, create a
              project and move machines into it.</span>
          </span>
          <button type="button" className="button-plain"
            onClick={() => props.notYet("New projects come with teams, in the next version.")}>
            New project
          </button>
        </div>
      )}

      <section className="pp-section" aria-labelledby="pp-notes">
        <div className="pp-section-head">
          <div className="pp-section-title">
            <h2 id="pp-notes">Notes</h2>
            <span>{project.personal
              ? "Only you see notes in Personal. Folders keep them in order"
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
                  {!project.personal && f.createdBy && (
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
    </main>
  );
}
