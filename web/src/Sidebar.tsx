// The sliding sidebar: one menu for everything, as approved on 11.10.
// Projects with their machines and terminals, a new project, the account and
// the theme. It slides over the page and does not push it, so that opening it
// never changes the terminal's width; pinned, it narrows the page once.

import { useState } from "react";
import type { Me, Project, Role, TerminalInfo } from "./api";
import {
  ChevronDown, Close, Computer, Dots, Logo, Moon, Pin, Plus, Server, Sun,
} from "./icons";
import type { Shown } from "./theme";

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
  notYet: (what: string) => void;
}

export function Sidebar(props: Props) {
  const { me, projects, pinned, shownTheme, notYet, onAddMachine } = props;
  const { terminals, currentTerminal, onOpenTerminal, onGoTo } = props;
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  const themeLabel =
    shownTheme === "light" ? "Switch to dark theme" : "Switch to light theme";

  return (
    <aside className="sidebar" aria-label="Sidebar"
      onMouseEnter={props.onMouseEnter} onMouseLeave={props.onMouseLeave}>
      <div className="sidebar-head">
        <Logo size={22} />
        <span className="brand">tTerm</span>
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
                  {project.machines.map((machine) => (
                    <li key={machine.id}>
                      <button type="button" className="tree-machine"
                        title={`Open a new terminal on ${machine.name}`}
                        onClick={() => onOpenTerminal(machine.id)}>
                        {machine.kind === "server" ? <Server size={15} />
                          : <Computer size={15} />}
                        <span>{machine.name}</span>
                      </button>
                      {terminals.some((t) => t.machineId === machine.id) && (
                        <ul className="tree-terminals">
                          {terminals.filter((t) => t.machineId === machine.id)
                            .map((t) => (
                              <li key={t.id}>
                                <button type="button" className="tree-terminal"
                                  aria-current={t.id === currentTerminal
                                    ? "page" : undefined}
                                  title={`Go to ${t.label}`}
                                  onClick={() => onGoTo(t.id)}>
                                  <span className="dot open" aria-hidden="true" />
                                  terminal {t.name}
                                </button>
                              </li>
                            ))}
                        </ul>
                      )}
                    </li>
                  ))}
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
        <div className="account">
          <span className="avatar" aria-hidden="true">{me.initials}</span>
          <span className="account-text">
            <strong>{me.firstName}</strong>
            <span>Account · {me.plan === "free" ? "Free plan" : me.plan}</span>
          </span>
        </div>
        <button type="button" className="icon-button"
          aria-label={themeLabel} title={themeLabel}
          onClick={props.onToggleTheme}>
          {shownTheme === "light" ? <Moon size={17} /> : <Sun size={17} />}
        </button>
      </div>
    </aside>
  );
}
