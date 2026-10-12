// "Add a machine", from the approved mockup: pick the project, run one
// command on the server, keep the window open, see it connect.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  installStatus, newInstall, type Install, type InstallStatus,
  type MachineKind, type Project,
} from "./api";
import { Check, Close, Computer, Copy, Server } from "./icons";

const POLL_MS = 1500;

interface Props {
  projects: Project[];
  projectId: string;
  kind: MachineKind;
  onClose: () => void;
  onConnected: () => void;
  onOpenTerminal: (machineId: string) => void;
  notYet: (what: string) => void;
}

function minutesLeft(expiresAt: string, now: number): number {
  return Math.max(0, Math.ceil((Date.parse(expiresAt) - now) / 60000));
}

export function AddMachine(props: Props) {
  const { projects, onClose, onConnected, notYet } = props;
  const addable = projects.filter((p) => p.role === "owner" || p.role === "admin");
  const [projectId, setProjectId] = useState(props.projectId);
  const [kind, setKind] = useState<MachineKind>(props.kind);
  const [install, setInstall] = useState<Install | null>(null);
  const [status, setStatus] = useState<InstallStatus | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [now, setNow] = useState(Date.now());
  const dialog = useRef<HTMLElement>(null);
  const announced = useRef(false);

  const project = projects.find((p) => p.id === projectId);

  const fresh = useCallback(() => {
    setInstall(null);
    setStatus(null);
    setProblem(null);
    setCopied(false);
    announced.current = false;
    newInstall(projectId)
      .then(setInstall)
      .catch((e: Error) => setProblem(e.message));
  }, [projectId]);

  // A command for the chosen project, made when the server side is shown.
  useEffect(() => {
    if (kind === "server") fresh();
  }, [kind, fresh]);

  // Watch the install until it connects or expires; after that nothing
  // more can change, so the asking stops.
  useEffect(() => {
    if (!install) return;
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      setNow(Date.now());
      try {
        const next = await installStatus(install.id);
        if (stopped) return;
        setStatus(next);
        if (next.state === "connected" || next.state === "expired") {
          window.clearInterval(timer);
        }
        if (next.state === "connected" && !announced.current) {
          announced.current = true;
          onConnected();
        }
      } catch {
        // The core may be restarting; the next tick tries again.
      }
    };
    timer = window.setInterval(tick, POLL_MS);
    tick();
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [install, onConnected]);

  const done = status?.state === "connected";

  useEffect(() => {
    dialog.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const copy = async () => {
    if (!install) return;
    try {
      await navigator.clipboard.writeText(install.command);
      setCopied(true);
    } catch {
      notYet("Could not copy: select the command and copy it by hand.");
    }
  };

  const state = status?.state ?? "waiting";
  const left = install ? minutesLeft(install.expiresAt, now) : 10;

  return (
    <div className="scrim">
      <section ref={dialog} className="dialog" role="dialog" aria-modal="true"
        aria-labelledby="am-title" tabIndex={-1}>
        <div className="dialog-head">
          <div className="dialog-title">
            <h2 id="am-title">Add a machine</h2>
            <div className="dialog-sub">
              <label htmlFor="am-project">To project</label>
              <select id="am-project" value={projectId} disabled={done}
                onChange={(e) => setProjectId(e.target.value)}>
                {addable.map((p) => (
                  <option key={p.id} value={p.id}>{p.code} · {p.name}</option>
                ))}
              </select>
            </div>
          </div>
          <button type="button" className="icon-button small" aria-label="Close"
            onClick={onClose}>
            <Close size={16} />
          </button>
        </div>

        {!done && (
          <div className="segmented" role="group" aria-label="Kind of machine">
            <button type="button" aria-pressed={kind === "server"}
              onClick={() => setKind("server")}>
              <Server size={15} /> Server
            </button>
            <button type="button" aria-pressed={kind === "computer"}
              onClick={() => setKind("computer")}>
              <Computer size={15} /> Computer
            </button>
          </div>
        )}

        {kind === "computer" && (
          <div className="note-box">
            Computers connect through a small agent. That comes in 0.0.6,
            together with agent 0.11.0. For now, add a server.
          </div>
        )}

        {kind === "server" && !done && (
          <>
            <ol className="dialog-steps">
              <li>
                <span className="step-number" aria-hidden="true">1</span>
                <div className="dialog-step">
                  <strong>Run this on the server</strong>
                  <div className="command-box">
                    <code>{install ? install.command : "…"}</code>
                    <button type="button" className="button-accent"
                      onClick={copy} disabled={!install}>
                      <Copy size={14} />
                      {copied ? "Copied" : "Copy"}
                    </button>
                  </div>
                  <span className="hint">
                    Works once, for the next {left} minute{left === 1 ? "" : "s"}.
                    Run it as root or as any user with sudo.
                  </span>
                </div>
              </li>
              <li>
                <span className="step-number" aria-hidden="true">2</span>
                <div className="dialog-step">
                  <strong>Keep this window open</strong>
                  <Progress state={state} problem={problem}
                    error={status?.error ?? null} onAgain={fresh}
                    project={project?.name ?? "the project"} />
                </div>
              </li>
            </ol>
            <details className="explain">
              <summary>What the script does</summary>
              <ul>
                <li>Creates the user tterm, with no password. If the tTerm
                  bot already made it, it is reused.</li>
                <li>Lets tTerm Web sign in as tterm with certificates that live
                  two minutes. No keys stay on the server.</li>
                <li>Gives tterm sudo, so you can manage the server. One command
                  takes it back.</li>
                <li>Never touches sshd_config and opens no ports. Running it
                  twice is safe.</li>
              </ul>
            </details>
          </>
        )}

        {done && status?.machine && (
          <div className="connected">
            <div className="connected-head">
              <span className="big-check" aria-hidden="true"><Check size={22} /></span>
              <div role="status" className="connected-text">
                <strong>{status.machine.name} is connected</strong>
                <span>
                  {[status.machine.os, status.machine.host,
                    `in tabs it reads ${status.machine.label}`]
                    .filter(Boolean).join(" · ")}
                </span>
              </div>
            </div>
            <div className="button-row">
              <button type="button" className="button-accent big"
                onClick={() => status.machine &&
                  props.onOpenTerminal(status.machine.id)}>
                Open a terminal
              </button>
              <button type="button" className="button-plain" onClick={fresh}>
                Add another machine
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function Progress({ state, problem, error, onAgain, project }: {
  state: string;
  problem: string | null;
  error: string | null;
  onAgain: () => void;
  project: string;
}) {
  if (problem) {
    return (
      <div className="error-box" role="alert">
        Could not make a command: {problem}
        <button type="button" className="button-plain" onClick={onAgain}>
          Try again
        </button>
      </div>
    );
  }
  if (state === "expired") {
    return (
      <div className="error-box" role="alert">
        This command has expired: it works for 10 minutes.
        <button type="button" className="button-plain" onClick={onAgain}>
          Get a new command
        </button>
      </div>
    );
  }
  if (state === "failed") {
    return (
      <div className="error-box" role="alert">
        <span>{error ?? "The server could not be checked."}</span>
        <span className="hint">The same command still works: fix this and
          run it again.</span>
      </div>
    );
  }
  return (
    <>
      <span role="status" className="waiting">
        <span className="spinner" aria-hidden="true" />
        {state === "connecting"
          ? "The server reported in. Signing in to check it…"
          : "Waiting for the server…"}
      </span>
      <span className="hint">
        It appears in {project} as soon as the script finishes, usually within
        a minute.
      </span>
    </>
  );
}
