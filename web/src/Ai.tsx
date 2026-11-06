// AI through MCP (0.1.29) in the page: Settings › Connections › AI, the
// window that says how to connect an AI, Change, the project page's AI and
// the terminal's «Claude is looking». As on the approved canvas (version
// 121: McpSettings, McpHow, McpProject, McpTerminal). An AI sees what the
// person allowed it, and only while it is connected; the machines under the
// keys only while the keys are open on the device it follows.

import { useEffect, useRef, useState } from "react";
import {
  aiLeaveProject, changeAi, CoreError, forgetAi, forgetAllAi, loadAi, settleProposal,
  type AiCan, type AiConnection, type AiProposal, type AiState, type AiWatching,
  type Project,
} from "./api";
import {
  AiChoice, AiKeys, choiceSummary, lockedNames, tickedMachines, type ChoiceProject,
  type ChoiceTerm, type Ticks,
} from "./AiChoice";
import { aiTitle, canChips, machinesLine, termUntil, timesLine, untilWords } from "./aiText";
import { list, useT, type Translate } from "./i18n";
import {
  Check, Close, Computer, Copy, Eye, Lock, Plus, Server, Spark, TerminalIcon,
} from "./icons";
import { hasOdd, oddParts, SETTLE_MS } from "./proposal";

/** The tabs of Connect an AI: where it is added. */
const CLIENTS = ["claude", "chatgpt", "code", "cursor", "other"] as const;
type Client = (typeof CLIENTS)[number];
const CLIENT_NAMES: Record<Client, string> = {
  claude: "Claude", chatgpt: "ChatGPT", code: "Claude Code", cursor: "Cursor", // i18n-ignore
  other: "",
};

/** An app on this computer: by where it takes the person back. */
function onThisComputer(c: AiConnection): boolean {
  return c.returnsTo === "127.0.0.1" || c.returnsTo === "localhost" || c.returnsTo === "::1";
}

/** Where it takes the person back, in a word: claude.ai, this computer,
 * an app. */
function placeWord(c: AiConnection, t: Translate): string {
  if (onThisComputer(c)) return t("connect_ai.place_here");
  if (c.returnsTo.endsWith(":")) return t("connect_ai.place_app", { scheme: c.returnsTo.slice(0, -1) });
  return c.returnsTo;
}

/** This device's time zone. */
function zone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

// ------------------------------------------------------------ the window

/** Connect an AI: tTerm's address, what to do in the AI, then Allow. */
export function AiHowDialog(props: { url: string; onClose: () => void }) {
  const t = useT();
  const [client, setClient] = useState<Client>("claude");
  const [copied, setCopied] = useState(false);
  const box = useRef<HTMLElement>(null);
  useEffect(() => {
    box.current?.querySelector<HTMLElement>("button")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      event.preventDefault();
      event.stopPropagation();
      props.onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);
  const copy = () => {
    void navigator.clipboard?.writeText(props.url).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  };
  const command = `claude mcp add --transport http tterm ${props.url}`; // i18n-ignore
  return (
    <div className="scrim" onMouseDown={(event) => {
      if (event.target === event.currentTarget && event.button === 0) props.onClose();
    }}>
      <section ref={box} className="dialog ai-how" role="dialog" aria-modal="true"
        aria-labelledby="ai-how-title">
        <div className="ai-dialog-top">
          <div className="ai-dialog-head">
            <h2 id="ai-how-title">{t("ai.how_title")}</h2>
            <p>{t("ai.how_lead")}</p>
          </div>
          <button type="button" className="icon-button" aria-label={t("common.close")}
            title={t("common.close")} onClick={props.onClose}>
            <Close size={17} />
          </button>
        </div>
        <ol className="ai-steps">
          <li>
            <span className="ai-step-n" aria-hidden="true">1</span>
            <div className="ai-step">
              <strong>{t("ai.how_copy")}</strong>
              <span className="ai-copy-row">
                <code className="ai-mono">{props.url}</code>
                <button type="button" className="button-plain small" onClick={copy}>
                  {copied ? <Check size={14} /> : <Copy size={14} />}
                  {copied ? t("ai.copied") : t("ai.copy")}
                </button>
              </span>
            </div>
          </li>
          <li>
            <span className="ai-step-n" aria-hidden="true">2</span>
            <div className="ai-step">
              <strong>{t("ai.how_add")}</strong>
              <div className="segmented ai-tabs" role="group" aria-label={t("ai.how_where")}>
                {CLIENTS.map((c) => (
                  <button key={c} type="button" aria-pressed={client === c}
                    onClick={() => setClient(c)}>
                    {c === "other" ? t("ai.how_other") : CLIENT_NAMES[c]}
                  </button>
                ))}
              </div>
              {client === "claude" && <p className="ai-step-text">{t("ai.how_claude")}</p>}
              {client === "chatgpt" && <p className="ai-step-text">{t("ai.how_chatgpt")}</p>}
              {client === "code" && (
                <>
                  <p className="ai-step-text">{t("ai.how_code_run")}</p>
                  <code className="ai-mono">{command}</code>
                  <p className="ai-step-text">
                    {t.rich("ai.how_code_then", { c: (inner) => <code>{inner}</code> })}
                  </p>
                </>
              )}
              {client === "cursor" && <p className="ai-step-text">{t("ai.how_cursor")}</p>}
              {client === "other" && (
                <>
                  <p className="ai-step-text">{t("ai.how_any")}</p>
                  <dl className="ai-facts">
                    <dt>{t("ai.how_address")}</dt><dd><code>{props.url}</code></dd>
                    <dt>{t("ai.how_transport")}</dt><dd>{t("ai.how_transport_value")}</dd>
                    <dt>{t("ai.how_signin")}</dt><dd>{t("ai.how_signin_value")}</dd>
                  </dl>
                </>
              )}
            </div>
          </li>
          <li>
            <span className="ai-step-n" aria-hidden="true">3</span>
            <div className="ai-step">
              <strong>{t("ai.how_allow")}</strong>
              <p className="ai-step-text">{t("ai.how_allow_text")}</p>
            </div>
          </li>
        </ol>
        <p className="ai-dialog-foot">{t("ai.how_keys")}</p>
      </section>
    </div>
  );
}

/** The projects with their machines, as the choice shows them. */
function choiceProjects(projects: Project[]): ChoiceProject[] {
  return projects.map((p) => ({
    id: p.id, name: p.name,
    machines: p.machines.map((m) => ({
      id: m.id, name: m.name, kind: m.kind, method: m.method ?? null,
      guarded: m.kind === "computer" || m.method === "key" || m.method === "mac",
    })),
  }));
}

/** Change: the same choice as on the allow page, saved here. */
export function AiChangeDialog(props: {
  connection: AiConnection; projects: Project[]; keys: AiState["keys"];
  onSaved: (c: AiConnection) => void; onClose: () => void;
}) {
  const t = useT();
  const c = props.connection;
  const projects = choiceProjects(props.projects);
  const [project, setProject] = useState(c.machines[0]?.projectId ?? projects[0]?.id ?? "");
  const [ticks, setTicks] = useState<Ticks>(() => {
    const out: Ticks = {};
    for (const m of c.machines) out[m.id] = [m.terminals, m.log];
    return out;
  });
  const [term, setTerm] = useState<ChoiceTerm>("keep");
  const [can, setCan] = useState<AiCan>({ ...c.can, see: true });
  const [proof, setProof] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const before = new Map(c.machines.map((m) => [m.id, m]));
  // Machines under the keys it did not see before, or saw less of.
  const added = projects.flatMap((p) => p.machines).filter((m) => m.guarded &&
    ((ticks[m.id]?.[0] && !before.get(m.id)?.terminals) ||
      (ticks[m.id]?.[1] && !before.get(m.id)?.log))).map((m) => m.name);
  const keysNeeded = added.length > 0 && props.keys.guarding && !props.keys.open && !proof;
  const none = tickedMachines(ticks).length === 0;
  const until = term === "keep" ? c.until : termUntil(term);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || busy) return;
      event.preventDefault();
      event.stopPropagation();
      props.onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [busy]);
  const save = async () => {
    setBusy(true);
    setProblem(null);
    try {
      props.onSaved(await changeAi(c.id, {
        machines: tickedMachines(ticks), can, term, zone: zone(), language: t.lang,
        ...(proof ? { auth: proof } : {}),
      }));
    } catch (error) {
      setBusy(false);
      setProblem(error instanceof CoreError && error.status === 423
        ? t("connect_ai.keys_first") : (error as Error).message);
    }
  };
  return (
    <div className="scrim" onMouseDown={(event) => {
      if (event.target === event.currentTarget && event.button === 0 && !busy) props.onClose();
    }}>
      <section className="dialog ai-change" role="dialog" aria-modal="true"
        aria-labelledby="ai-change-title">
        <div className="ai-dialog-top">
          <div className="ai-dialog-head">
            <h2 id="ai-change-title">{t("ai.change_title", { ai: c.name })}</h2>
            <p>{aiTitle(c.name, c.returnsTo, t)}</p>
          </div>
          <button type="button" className="icon-button" aria-label={t("common.close")}
            title={t("common.close")} disabled={busy} onClick={props.onClose}>
            <Close size={17} />
          </button>
        </div>
        <AiChoice ai={c.name} projects={projects} project={project} onProject={setProject}
          ticks={ticks} onTicks={setTicks} term={term} onTerm={setTerm}
          can={can} onCan={setCan}
          keep={{ until: c.until }} disabled={busy} idPrefix="ac" locks={props.keys.guarding}
          keys={props.keys.guarding && added.length ? (
            <AiKeys ai={c.name} locked={added} open={props.keys.open} proof={proof}
              password onProof={setProof} idPrefix="ac" />
          ) : null} />
        <p className="ai-summary-main">
          {choiceSummary(c.name, projects, ticks, until, t)}
          {lockedNames(projects, ticks).length > 0 && (props.keys.open || !!proof) && (
            <> {t("ai.change_keys_here", { ai: c.name })}</>
          )}
        </p>
        <div className="ai-actions">
          {(none || keysNeeded) && (
            <span className="ai-why" role="status">
              {none ? t("connect_ai.why_none") : t("connect_ai.why_keys")}
            </span>
          )}
          <button type="button" className="button-plain" disabled={busy} onClick={props.onClose}>
            {t("common.cancel")}
          </button>
          <button type="button" className="button-accent big" disabled={busy || none || keysNeeded}
            onClick={() => void save()}>
            {t("ai.save")}
          </button>
        </div>
        {problem && <div className="error-box" role="alert">{problem}</div>}
      </section>
    </div>
  );
}

// ----------------------------------------------------------- the settings

function AiIcon(props: { c: AiConnection; size?: number }) {
  return onThisComputer(props.c) ? <TerminalIcon size={props.size ?? 18} />
    : <Spark size={props.size ?? 18} />;
}

/** The machines under the keys it cannot see now, and why. */
function keysLine(c: AiConnection, t: Translate): string | null {
  const guarded = c.machines.filter((m) => m.guarded).map((m) => m.name);
  if (!guarded.length) return null;
  const names = list([...new Set(guarded)], t.lang);
  const where = c.keysHere ? t("ai.keys_where_here")
    : c.keysDevice?.label ? t("ai.keys_where_device", { device: c.keysDevice.label })
      : t("ai.keys_where_browser");
  return c.keysOpen ? t("ai.keys_seen", { machines: names, where })
    : t("ai.keys_not_seen", { machines: names, where });
}

interface SectionProps {
  /** The projects, with their machines, for Change. */
  projects: Project[];
  notify: (text: string) => void;
  /** Something changed: the page's terminals hear who may see them. */
  onChanged: () => void;
}

/** Settings › Connections › AI. */
export function AiSection(props: SectionProps) {
  const t = useT();
  const [state, setState] = useState<AiState | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [how, setHow] = useState(false);
  const [changing, setChanging] = useState<AiConnection | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = () => loadAi().then(setState).catch((error: Error) => setProblem(error.message));
  useEffect(() => {
    void load();
  }, []);

  const forget = async (c: AiConnection) => {
    setProblem(null);
    try {
      await forgetAi(c.id);
      setState((was) => was && { ...was, connections: was.connections.filter((x) => x.id !== c.id) });
      props.notify(t("ai.forgotten", { ai: c.name }));
      props.onChanged();
    } catch (error) {
      setProblem((error as Error).message);
    }
  };
  const forgetAll = async () => {
    setBusy(true);
    setProblem(null);
    try {
      const n = await forgetAllAi();
      setState((was) => was && { ...was, connections: [] });
      setAsking(false);
      props.notify(t("ai.all_forgotten", { n }));
      props.onChanged();
    } catch (error) {
      setProblem((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!state) {
    return (
      <section className="account-section">
        <div className="account-section-head"><h2>{t("ai.title")}</h2></div>
        <div className="account-card devices-card">
          {problem ? <div className="error-box" role="alert">{problem}</div>
            : <div className="waiting"><span className="spinner" />{t("common.loading")}</div>}
        </div>
      </section>
    );
  }
  const connect = (
    <button type="button" className="button-plain small" aria-haspopup="dialog"
      onClick={() => setHow(true)}>
      {t("ai.connect")}
    </button>
  );
  return (
    <section className="account-section" aria-labelledby="ac-ai">
      <div className="account-section-head ai-section-head">
        <div className="ai-section-title">
          <h2 id="ac-ai">{t("ai.title")}</h2>
          <p>{t("ai.about")}</p>
        </div>
        {state.connections.length > 0 && connect}
      </div>
      <div className="account-card devices-card">
        {state.connections.length === 0 ? (
          <div className="forget-all ai-none">
            <span className="forget-all-text">
              <strong>{t("ai.none")}</strong>
              <span>{t("ai.none_text")}</span>
            </span>
            {connect}
          </div>
        ) : (
          <ul className="device-list">
            {state.connections.map((c) => {
              const keys = state.keys.guarding ? keysLine(c, t) : null;
              return (
                <li key={c.id} className="device-row ai-row">
                  <span className="device-icon" aria-hidden="true"><AiIcon c={c} /></span>
                  <span className="device-text">
                    <strong>
                      {aiTitle(c.name, c.returnsTo, t)}
                      {canChips(c.can, t).map((chip) => (
                        <span key={chip} className="chip">{chip}</span>
                      ))}
                    </strong>
                    <span>{c.machines.length ? machinesLine(c.machines, t) : t("ai.sees_nothing")}</span>
                    <span>{timesLine(c.until, c.lastCall, t)}</span>
                    {keys && (
                      <span className={c.keysOpen ? "ai-keys-line" : "ai-keys-line closed"}>
                        <Lock size={12} /><span>{keys}</span>
                      </span>
                    )}
                  </span>
                  <span className="ai-row-buttons">
                    <button type="button" className="button-plain small"
                      onClick={() => setChanging(c)}>
                      {t("ai.change")}
                    </button>
                    <button type="button" className="button-plain small"
                      onClick={() => void forget(c)}>
                      {t("ai.forget")}
                    </button>
                  </span>
                </li>
              );
            })}
          </ul>
        )}
        {state.connections.length > 0 && (
          <div className="forget-all">
            <span className="forget-all-text">
              <strong>{t("ai.forget_all")}</strong>
              <span>{t("ai.forget_all_text")}</span>
            </span>
            {asking ? (
              <span className="button-row">
                <button type="button" className="button-danger big" disabled={busy}
                  onClick={() => void forgetAll()}>
                  {t("ai.forget_all")}
                </button>
                <button type="button" className="button-plain" onClick={() => setAsking(false)}>
                  {t("common.cancel")}
                </button>
              </span>
            ) : (
              <button type="button" className="button-plain" onClick={() => setAsking(true)}>
                {t("ai.forget_all_ask")}
              </button>
            )}
          </div>
        )}
        {problem && <div className="error-box" role="alert">{problem}</div>}
      </div>
      {how && <AiHowDialog url={state.url} onClose={() => setHow(false)} />}
      {changing && (
        <AiChangeDialog connection={changing} projects={props.projects} keys={state.keys}
          onClose={() => setChanging(null)}
          onSaved={(saved) => {
            setChanging(null);
            setState((was) => was && { ...was, connections: was.connections.map((x) =>
              x.id === saved.id ? saved : x) });
            props.notify(t("ai.saved", { ai: saved.name }));
            props.onChanged();
          }} />
      )}
    </section>
  );
}

// ---------------------------------------------------- the project's page

/** The project's page: the AI that sees its machines (McpProject). */
export function AiProjectSection(props: {
  project: Project; projects: Project[]; notify: (text: string) => void;
  onChanged: () => void;
}) {
  const t = useT();
  const [state, setState] = useState<AiState | null>(null);
  const [how, setHow] = useState(false);
  const [changing, setChanging] = useState<AiConnection | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    loadAi().then(setState).catch((error: Error) => setProblem(error.message));
  }, [props.project.id]);
  if (!state) {
    return problem ? <div className="error-box" role="alert">{problem}</div> : null;
  }
  const here = state.connections.map((c) => ({
    c, machines: c.machines.filter((m) => m.projectId === props.project.id),
  })).filter((x) => x.machines.length > 0);
  const leave = async (c: AiConnection) => {
    setProblem(null);
    try {
      await aiLeaveProject(c.id, props.project.id);
      setState((was) => was && { ...was, connections: was.connections.map((x) => x.id === c.id
        ? { ...x, machines: x.machines.filter((m) => m.projectId !== props.project.id) } : x) });
      props.notify(t("ai.left_project", { ai: c.name, project: props.project.name }));
      props.onChanged();
    } catch (error) {
      setProblem((error as Error).message);
    }
  };
  return (
    <section className="pp-section ai-project" aria-labelledby="pp-ai">
      <div className="pp-section-head">
        <div className="pp-section-title">
          <h2 id="pp-ai">{t("ai.project_title")}</h2>
          <span>{t("ai.project_about")}</span>
        </div>
        <button type="button" className="button-plain small" aria-haspopup="dialog"
          onClick={() => setHow(true)}>
          <Plus size={14} />{t("ai.project_connect")}
        </button>
      </div>
      {here.length === 0 ? (
        <p className="ai-small">{t("ai.project_none")}</p>
      ) : here.map(({ c, machines }) => (
        <div key={c.id} className="ai-project-card">
          <div className="ai-project-top">
            <span className="ai-badge" aria-hidden="true"><AiIcon c={c} /></span>
            <span className="ai-project-name">
              <span className="ai-project-line">
                <strong>{c.name}</strong>
                <span className="ai-small">{placeWord(c, t)}</span>
                <span className="ai-small">{t("ai.allowed_by_you")}</span>
              </span>
              <span className="ai-chips">
                {canChips(c.can, t).map((chip) => <span key={chip} className="chip">{chip}</span>)}
              </span>
            </span>
            <span className="ai-row-buttons">
              <button type="button" className="button-plain small" onClick={() => setChanging(c)}>
                {t("ai.change")}
              </button>
              <button type="button" className="button-plain small"
                title={t("ai.leave_title", { ai: c.name, project: props.project.name })}
                onClick={() => void leave(c)}>
                {t("ai.leave")}
              </button>
            </span>
          </div>
          <div role="table" className="ai-project-table"
            aria-label={t("ai.project_table", { ai: c.name, project: props.project.name })}>
            <div role="row" className="ai-project-row head">
              <span role="columnheader">{t("ai.machine")}</span>
              <span role="columnheader">{t("ai.terminals")}</span>
              <span role="columnheader">{t("ai.log")}</span>
            </div>
            {machines.map((m) => (
              <div key={m.id} role="row" className="ai-project-row">
                <span role="cell" className="ai-project-machine">
                  {m.kind === "computer" ? <Computer size={14} /> : <Server size={14} />}
                  <span className="ai-project-mname">{m.name}</span>
                  {m.guarded && state.keys.guarding && (
                    <span className="ai-lock" title={t("ai.keys_follow")}>
                      <Lock size={12} />{t("ai.while_open")}
                    </span>
                  )}
                </span>
                <span role="cell" className="ai-yes">{m.terminals
                  ? <span role="img" aria-label={t("ai.yes")}><Check size={15} /></span>
                  : <span aria-label={t("ai.no")}>–</span>}</span>
                <span role="cell" className="ai-yes">{m.log
                  ? <span role="img" aria-label={t("ai.yes")}><Check size={15} /></span>
                  : <span aria-label={t("ai.no")}>–</span>}</span>
              </div>
            ))}
          </div>
          <p className="ai-project-foot">{timesLine(c.until, c.lastCall, t)}</p>
        </div>
      ))}
      <p className="ai-small">{t("ai.project_new_machines")}</p>
      {problem && <div className="error-box" role="alert">{problem}</div>}
      {how && <AiHowDialog url={state.url} onClose={() => setHow(false)} />}
      {changing && (
        <AiChangeDialog connection={changing} projects={props.projects} keys={state.keys}
          onClose={() => setChanging(null)}
          onSaved={(saved) => {
            setChanging(null);
            setState((was) => was && { ...was, connections: was.connections.map((x) =>
              x.id === saved.id ? saved : x) });
            props.notify(t("ai.saved", { ai: saved.name }));
            props.onChanged();
          }} />
      )}
    </section>
  );
}

// ------------------------------------------------------ the terminal's chip

/** «Claude is looking», in the bar, for the terminal in front: the AI that
 * may see its machine's terminals; its window says who, what and how long,
 * with Forget and Settings › AI. */
export function AiChip(props: {
  watching: AiWatching[]; machineId: string | null; guarded: boolean;
  onForget: (w: AiWatching) => void; onSettings: () => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const here = props.machineId
    ? props.watching.filter((w) => w.machines.includes(props.machineId!)) : [];
  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => {
      if (!wrap.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  if (!here.length) return null;
  const label = here.length === 1 ? t("ai.chip_one", { ai: here[0].name })
    : t("ai.chip_more", { ai: here[0].name, n: here.length - 1 });
  return (
    <div ref={wrap} className="ai-chip-wrap">
      <button type="button" className="ai-chip" aria-haspopup="dialog" aria-expanded={open}
        title={t("ai.chip_title", { ai: list(here.map((w) => w.name), t.lang) })}
        onClick={() => setOpen((was) => !was)}>
        <Eye size={14} />{label}
      </button>
      {open && (
        <div className="ai-pop" role="dialog" aria-labelledby="ai-pop-title">
          {here.map((w) => (
            <div key={w.id} className="ai-pop-one">
              <div className="ai-pop-top">
                <span className="ai-badge" aria-hidden="true"><Spark size={18} /></span>
                <span className="ai-pop-name">
                  <strong id="ai-pop-title">{w.name}</strong>
                  <span>{[...canChips(w.can, t), untilWords(w.until, t)].join(" · ")}</span>
                </span>
              </div>
              <p>{t(w.can.propose && w.can.notes ? "ai.pop_text_both"
                : w.can.propose ? "ai.pop_text_propose"
                  : w.can.notes ? "ai.pop_text_notes" : "ai.pop_text", { ai: w.name })}</p>
              {props.guarded && !w.keysOpen && (
                <p className="ai-keys-line closed"><Lock size={12} />
                  <span>{t("ai.pop_closed", { ai: w.name })}</span></p>
              )}
              <div className="ai-pop-buttons">
                <button type="button" className="button-plain small danger"
                  onClick={() => {
                    setOpen(false);
                    props.onForget(w);
                  }}>
                  {t("ai.pop_forget", { ai: w.name })}
                </button>
                <button type="button" className="button-plain small" onClick={() => {
                  setOpen(false);
                  props.onSettings();
                }}>
                  {t("ai.pop_settings")}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** The eye at a block the AI read: «Claude». */
export function AiRead(props: { read: { id: string; name: string }[] | undefined }) {
  const t = useT();
  if (!props.read?.length) return null;
  const names = [...new Set(props.read.map((r) => r.name))];
  return (
    <span className="block-ai" title={t("ai.read_title", { ai: list(names, t.lang) })}>
      <Eye size={12} />{list(names, t.lang)}
    </span>
  );
}

/** The eye at a terminal in the sidebar. */
export function AiEye(props: { names: string[] }) {
  const t = useT();
  if (!props.names.length) return null;
  const who = list(props.names, t.lang);
  return (
    <>
      <span className="sb-ai" title={t("ai.chip_title", { ai: who })}><Eye size={13} /></span>
      <span className="visually-hidden">{t("ai.eye_hidden", { ai: who })}</span>
    </>
  );
}

/** A command an AI proposed in this terminal (0.1.30), as on the canvas
 * (version 121, McpTerminal): it waits here until the person runs it, puts
 * it into the line or removes it. The AI runs nothing. Run goes through the
 * terminal like a command typed, its question for a destructive one too.
 *
 * From the security review: Run and Insert wait a moment after it shows,
 * and after another takes its place (which it then says), so a click meant
 * for what was there does not land on it; the command reads left to right
 * as it will be typed, whatever its letters; characters beyond ASCII in a
 * word with Latin letters are marked, a lookalike shows. */
export function ProposalBlock(props: {
  proposal: AiProposal;
  /** At the prompt: a command can run, or go into the line. */
  ready: boolean;
  onRun: () => void;
  onInsert: (command: string) => void;
  onGone: (id: string) => void;
  notify: (text: string) => void;
}) {
  const t = useT();
  const p = props.proposal;
  const [busy, setBusy] = useState(false);
  const [settled, setSettled] = useState<string | null>(null);
  const [replaced, setReplaced] = useState(false);
  const shown = useRef<string | null>(null);
  useEffect(() => {
    if (shown.current !== null && shown.current !== p.id) setReplaced(true);
    shown.current = p.id;
    const timer = window.setTimeout(() => setSettled(p.id), SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [p.id]);
  const settling = settled !== p.id;
  const settle = async (what: "insert" | "remove") => {
    setBusy(true);
    try {
      await settleProposal(p.id, what);
      if (what === "insert") props.onInsert(p.command);
      props.onGone(p.id);
    } catch (error) {
      if (error instanceof CoreError && (error.status === 409 || error.status === 410)) {
        props.onGone(p.id);
      }
      props.notify(error instanceof CoreError && error.status === 425
        ? t("feed.proposal_early") : (error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const wait = props.ready ? undefined : t("feed.proposal_wait");
  const parts = oddParts(p.command);
  return (
    <section className={replaced ? "ai-proposal replaced" : "ai-proposal"}
      aria-label={t("feed.proposal_label", { ai: p.ai })}>
      <div className="ai-proposal-head">
        <Spark size={15} />
        <span>{t("feed.proposes", { ai: p.ai })}</span>
        <span className="ai-proposal-state" aria-live="polite">
          {t(replaced ? "feed.proposal_new" : "feed.proposal_waiting")}
        </span>
      </div>
      {p.why && <p className="ai-proposal-why">{p.why}</p>}
      <div className="ai-proposal-row">
        <code className="block-command ai-proposal-command">
          {parts.map((part, i) => part.odd
            ? <mark key={i} className="ai-proposal-odd">{part.text}</mark>
            : part.text)}
        </code>
        <button type="button" className="button-accent"
          disabled={busy || !props.ready || settling}
          title={wait} onClick={props.onRun}>{t("feed.proposal_run")}</button>
        <button type="button" className="button-plain small"
          disabled={busy || !props.ready || settling}
          title={wait} onClick={() => void settle("insert")}>{t("feed.proposal_insert")}</button>
        <button type="button" className="button-ghost" disabled={busy}
          onClick={() => void settle("remove")}>{t("feed.proposal_remove")}</button>
      </div>
      {hasOdd(parts) && <p className="ai-proposal-mixed">{t("feed.proposal_mixed")}</p>}
    </section>
  );
}
