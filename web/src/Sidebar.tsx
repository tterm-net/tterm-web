// The sliding sidebar: one menu for everything, as approved on 11.10.
// Projects with their machines and terminals, a new project, the account and
// the theme. It slides over the page and does not push it, so that opening it
// never changes the terminal's width; pinned, it narrows the page once.
//
// A machine opens a new terminal on it; a terminal goes to its tab, opening
// the tab again if it was closed. Pointed at, a machine offers Remove and a
// terminal End (from 0.0.8); both ask first.
//
// The account at the bottom opens its menu (from 0.0.9): Devices, Sign out.

import { useEffect, useRef, useState } from "react";
import type { Machine, Me, Project, Role, TerminalInfo } from "./api";
import {
  ChevronDown, Close, Computer, Devices, Dots, Logo, Moon, Pin, Plus, Server,
  SignOut, Sun, Trash,
} from "./icons";
import type { Dot } from "./TabBar";
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
  onAddMachine: (projectId: string) => void;
  terminals: TerminalInfo[];
  currentTerminal?: string;
  onOpenTerminal: (machineId: string) => void;
  onGoTo: (terminalId: string) => void;
  onRemoveMachine: (machine: Machine) => void;
  onEndTerminal: (terminal: TerminalInfo) => void;
  /** The machines page. */
  onHome: () => void;
  dotFor: (terminal: TerminalInfo) => Dot;
  notYet: (what: string) => void;
  onDevices: () => void;
  onSignOut: () => void;
}

export function Sidebar(props: Props) {
  const { me, projects, pinned, shownTheme, notYet, onAddMachine } = props;
  const { terminals, currentTerminal, onOpenTerminal, onGoTo, dotFor } = props;
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  const themeLabel =
    shownTheme === "light" ? "Switch to dark theme" : "Switch to light theme";

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
          const open = !folded[project.id];
          const canAdd = project.role === "owner" || project.role === "admin";
          return (
            <div key={project.id}>
              <div className="project-row">
                <button type="button" className="chevron"
                  aria-expanded={open}
                  aria-label={`${open ? "Fold" : "Unfold"} ${project.name}`}
                  onClick={() =>
                    setFolded({ ...folded, [project.id]: open })}>
                  <ChevronDown size={14} />
                </button>
                <button type="button" className="project-name"
                  title={`Open the ${project.name} overview`}
                  onClick={() =>
                    notYet("The project overview comes with teams, in stage 3.")}>
                  <span className="code-badge" aria-hidden="true">
                    {project.code}
                  </span>
                  <span className="project-title">{project.name}</span>
                  <span className="role">{ROLE_LABEL[project.role]}</span>
                </button>
                <button type="button" className="icon-button tiny"
                  aria-label={`${project.name}: project menu`}
                  aria-haspopup="menu"
                  onClick={() =>
                    notYet("The project menu comes with teams, in stage 3.")}>
                  <Dots />
                </button>
              </div>
              {open && project.machines.length === 0 && (
                <div className="empty-machines">
                  <p>No machines yet. Add a server or a computer to open
                    terminals on it.</p>
                  {canAdd && (
                    <button type="button" className="button-accent"
                      onClick={() => onAddMachine(project.id)}>
                      <Plus size={14} />
                      Add machine
                    </button>
                  )}
                </div>
              )}
              {open && project.machines.length > 0 && (
                <ul className="tree">
                  {project.machines.map((machine) => {
                    const offline = machine.kind === "computer" && !machine.online;
                    const seen = offline ? lastSeen(machine.lastSeen) : "";
                    return (
                    <li key={machine.id}>
                      <div className="tree-row">
                        <button type="button"
                          className={offline ? "tree-machine offline" : "tree-machine"}
                          title={offline
                            ? `${machine.name} is offline: asleep, or its agent is not running${seen ? ` (${seen})` : ""}`
                            : `Open a new terminal on ${machine.name}`}
                          onClick={() => offline
                            ? notYet(`${machine.name} is offline${seen ? `, ${seen}` : ""}: it comes back when its agent connects.`)
                            : onOpenTerminal(machine.id)}>
                          {machine.kind === "server" ? <Server size={15} />
                            : <Computer size={15} />}
                          <span>{machine.name}</span>
                        </button>
                        {canAdd && (
                          <button type="button" className="tree-action"
                            aria-label={`Remove ${machine.name}`}
                            title={`Remove ${machine.name} from tTerm Web`}
                            onClick={() => props.onRemoveMachine(machine)}>
                            <Trash size={14} />
                          </button>
                        )}
                      </div>
                      {terminals.some((t) => t.machineId === machine.id) && (
                        <ul className="tree-terminals">
                          {terminals.filter((t) => t.machineId === machine.id)
                            .map((t) => (
                              <li key={t.id}>
                                <div className="tree-row">
                                  <button type="button" className="tree-terminal"
                                    aria-current={t.id === currentTerminal
                                      ? "page" : undefined}
                                    title={dotFor(t) === "away"
                                      ? `Go to ${t.label}: it waits for ${machine.name} to come back`
                                      : `Go to ${t.label}`}
                                    onClick={() => onGoTo(t.id)}>
                                    <span className={`dot ${dotFor(t)}`}
                                      aria-hidden="true" />
                                    terminal {t.name}
                                  </button>
                                  <button type="button" className="tree-action"
                                    aria-label={`End ${t.label}`}
                                    title={`End ${t.label}: its shell ends`}
                                    onClick={() => props.onEndTerminal(t)}>
                                    <Close size={13} />
                                  </button>
                                </div>
                              </li>
                            ))}
                        </ul>
                      )}
                    </li>
                    );
                  })}
                  {canAdd && (
                    <li>
                      <button type="button" className="tree-add"
                        onClick={() => onAddMachine(project.id)}>
                        <Plus size={15} />
                        Add machine
                      </button>
                    </li>
                  )}
                </ul>
              )}
            </div>
          );
        })}
        <button type="button" className="new-project"
          onClick={() => notYet("New projects come with teams, in stage 3.")}>
          <span className="dashed-tile" aria-hidden="true">
            <Plus size={13} />
          </span>
          New project
        </button>
      </div>

      <div className="sidebar-foot">
        <AccountMenu me={me} onDevices={props.onDevices}
          onSignOut={props.onSignOut} />
        <button type="button" className="icon-button"
          aria-label={themeLabel} title={themeLabel}
          onClick={props.onToggleTheme}>
          {shownTheme === "light" ? <Moon size={17} /> : <Sun size={17} />}
        </button>
      </div>
    </aside>
  );
}

/** The account at the bottom of the sidebar, and its menu. */
function AccountMenu(props: {
  me: Me;
  onDevices: () => void;
  onSignOut: () => void;
}) {
  const { me } = props;
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const plan = me.plan === "free" ? "Free plan" : me.plan;

  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", away);
    window.addEventListener("keydown", onKey, true);
    box.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
    return () => {
      document.removeEventListener("mousedown", away);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  const pick = (then: () => void) => () => {
    setOpen(false);
    then();
  };

  return (
    <div className="account-box" ref={box}>
      {open && (
        <div className="account-menu" role="menu" aria-label="Account">
          <div className="account-menu-head">
            <strong>{me.name}</strong>
            {me.email && <span>{me.email}</span>}
            <span>{plan}</span>
          </div>
          <button type="button" role="menuitem" className="menu-item"
            onClick={pick(props.onDevices)}>
            <Devices size={15} />
            Devices
          </button>
          <button type="button" role="menuitem" className="menu-item"
            onClick={pick(props.onSignOut)}>
            <SignOut size={15} />
            Sign out
          </button>
        </div>
      )}
      <button type="button" className="account" aria-haspopup="menu"
        aria-expanded={open} title={me.email ?? undefined}
        onClick={() => setOpen((was) => !was)}>
        <span className="avatar" aria-hidden="true">{me.initials}</span>
        <span className="account-text">
          <strong>{me.firstName}</strong>
          <span>Account · {plan}</span>
        </span>
      </button>
    </div>
  );
}
