// The machines page: each machine with its passport, what tTerm Web learned
// about it over SSH when it connected, a button for a new terminal, and the
// terminals already running on it.

import type { Machine, Passport, Project, TerminalInfo } from "./api";
import { Computer, Plus, Server, TerminalIcon } from "./icons";

interface Props {
  projects: Project[];
  terminals: TerminalInfo[];
  onAdd: (projectId: string) => void;
  onOpenTerminal: (machineId: string) => void;
  onGoTo: (terminalId: string) => void;
}

function gb(kb?: number): string | null {
  if (!kb) return null;
  const value = kb / 1024 / 1024;
  return value >= 10 ? `${Math.round(value)} GB` : `${value.toFixed(1)} GB`;
}

function facts(p: Passport): [string, string][] {
  const rows: [string, string | null][] = [
    ["System", [p.os, p.arch].filter(Boolean).join(", ") || null],
    ["Kernel", p.kernel ?? null],
    ["CPU", p.cpus ? `${p.cpus} core${p.cpus === 1 ? "" : "s"}` : null],
    ["Memory", gb(p.memory_kb)],
    ["Disk", p.disk_total_kb
      ? `${gb(p.disk_free_kb) ?? "?"} free of ${gb(p.disk_total_kb)}` : null],
    ["Packages", p.package_manager ?? null],
    ["Shell", p.shell ? `${p.shell}${p.shells?.length
      ? ` (has ${p.shells.join(", ")})` : ""}` : null],
    ["sudo", p.sudo === undefined ? null : p.sudo ? "works" : "not available"],
    ["Commands", p.commands_count ? `${p.commands_count} known` : null],
  ];
  return rows.filter((row): row is [string, string] => row[1] !== null);
}

function MachineCard({ machine, code, terminals, onOpenTerminal, onGoTo }: {
  machine: Machine;
  code: string;
  terminals: TerminalInfo[];
  onOpenTerminal: (machineId: string) => void;
  onGoTo: (terminalId: string) => void;
}) {
  const passport = machine.passport ?? {};
  return (
    <article className="machine-card" aria-label={machine.name}>
      <div className="machine-top">
        <span className="tile" aria-hidden="true">
          {machine.kind === "server" ? <Server size={20} /> : <Computer size={20} />}
        </span>
        <div className="machine-names">
          <strong>{machine.name}</strong>
          <span>{[machine.os, machine.host].filter(Boolean).join(" · ")}</span>
        </div>
        <span className="chip">{code}/{machine.name}</span>
      </div>
      <dl className="facts">
        {facts(passport).map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div className="button-row">
        <button type="button" className="button-accent"
          onClick={() => onOpenTerminal(machine.id)}>
          Open a terminal
        </button>
        {terminals.map((t) => (
          <button key={t.id} type="button" className="button-plain running"
            title={`Back to ${t.label}, still running`}
            onClick={() => onGoTo(t.id)}>
            <TerminalIcon size={14} />
            {t.label}
          </button>
        ))}
      </div>
    </article>
  );
}

export function Machines(props: Props) {
  const { projects, terminals, onAdd, onOpenTerminal, onGoTo } = props;
  const withMachines = projects.filter((p) => p.machines.length > 0);
  const first = projects.find((p) => p.role === "owner") ?? projects[0];
  return (
    <main className="machines-view">
      <div className="machines-head">
        <h1>Machines</h1>
        {first && (
          <button type="button" className="button-accent big"
            onClick={() => onAdd(first.id)}>
            <Plus size={14} /> Add a machine
          </button>
        )}
      </div>
      <p className="lead small">
        A terminal keeps running when you close its tab: it waits here and in
        the sidebar until its shell ends.
      </p>
      {withMachines.map((project) => (
        <section key={project.id} className="machines-project"
          aria-label={project.name}>
          <h2>
            <span className="code-badge" aria-hidden="true">{project.code}</span>
            {project.name}
          </h2>
          <div className="machine-list">
            {project.machines.map((machine) => (
              <MachineCard key={machine.id} machine={machine}
                code={project.code} onOpenTerminal={onOpenTerminal}
                onGoTo={onGoTo}
                terminals={terminals.filter((t) => t.machineId === machine.id)} />
            ))}
          </div>
        </section>
      ))}
    </main>
  );
}
