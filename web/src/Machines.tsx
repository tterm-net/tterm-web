// What the page shows once machines are connected and before terminals come
// (0.0.4): each machine and its passport, what tTerm Web learned about it
// over SSH when it connected.

import type { Machine, Passport, Project } from "./api";
import { Computer, Plus, Server } from "./icons";

interface Props {
  projects: Project[];
  onAdd: (projectId: string) => void;
  notYet: (what: string) => void;
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

function MachineCard({ machine, code, notYet }: {
  machine: Machine; code: string; notYet: (what: string) => void;
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
          onClick={() => notYet("Terminals come in 0.0.4.")}>
          Open a terminal
        </button>
      </div>
    </article>
  );
}

export function Machines({ projects, onAdd, notYet }: Props) {
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
        Terminals open from here in 0.0.4. For now, this is what tTerm Web
        learned about each machine when it connected.
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
                code={project.code} notYet={notYet} />
            ))}
          </div>
        </section>
      ))}
    </main>
  );
}
