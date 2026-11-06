// What an AI may see and do (0.1.29), as the person chooses it: on the page
// an AI app opens to connect (/connect-ai, ConnectAi.tsx) and in Change in
// Settings › AI (Ai.tsx). As on the approved canvas (version 121, McpAllow):
// the project, its machines each with its terminals and its log (both
// ticked at first), what the AI may do, and how long, as a list; machines
// under the keys need them open on this device, or the vault's password
// right here (AiKeys).

import { useState, type ReactNode } from "react";
import {
  loadVault, openVaultPassword, type LoginMethod, type MachineKind,
} from "./api";
import { machineHow, TERMS, termOption, untilWords, type Term } from "./aiText";
import { list, useT, type Translate } from "./i18n";
import { Check, ChevronDown, Computer, Cross, Lock, Server } from "./icons";
import { b64u, wipe } from "./vault/bytes";
import { passwordKeys, passwordSettings } from "./vault/crypto";
import { passwordWords, VaultError } from "./vault/store";
import { problemText } from "./VaultDialogs";

export interface ChoiceMachine {
  id: string;
  name: string;
  kind: MachineKind;
  method: LoginMethod | null;
  guarded: boolean;
}

export interface ChoiceProject {
  id: string;
  name: string;
  machines: ChoiceMachine[];
}

/** Each machine ticked: its terminals, its log. */
export type Ticks = Record<string, [boolean, boolean]>;

export type ChoiceTerm = Term | "keep";

/** The machines ticked, with what of each. */
export function tickedMachines(ticks: Ticks) {
  return Object.entries(ticks).filter(([, [a, b]]) => a || b)
    .map(([id, [terminals, log]]) => ({ id, terminals, log }));
}

/** The machines under the keys among those ticked, by name. */
export function lockedNames(projects: ChoiceProject[], ticks: Ticks): string[] {
  return projects.flatMap((p) => p.machines)
    .filter((m) => m.guarded && (ticks[m.id]?.[0] || ticks[m.id]?.[1]))
    .map((m) => m.name);
}

/** In one sentence: what the AI will be able to do, for how long. */
export function choiceSummary(ai: string, projects: ChoiceProject[], ticks: Ticks,
  until: number | null, t: Translate): string {
  const ticked = projects.map((p) => ({
    name: p.name,
    n: p.machines.filter((m) => ticks[m.id]?.[0] || ticks[m.id]?.[1]).length,
  })).filter((p) => p.n > 0);
  const n = ticked.reduce((sum, p) => sum + p.n, 0);
  if (!n) return t("ai.summary_none");
  return t("ai.summary", { ai, n, projects: list(ticked.map((p) => p.name), t.lang),
    until: untilWords(until, t) });
}

/** The machines of a project ticked as at first: all, both. */
export function allTicked(project: ChoiceProject | undefined): Ticks {
  const out: Ticks = {};
  for (const m of project?.machines ?? []) out[m.id] = [true, true];
  return out;
}

interface Props {
  /** The AI's name: Claude. */
  ai: string;
  projects: ChoiceProject[];
  project: string;
  onProject: (id: string) => void;
  ticks: Ticks;
  onTicks: (ticks: Ticks) => void;
  term: ChoiceTerm;
  onTerm: (term: ChoiceTerm) => void;
  /** In Change: until when it is now; "keep" leaves it. */
  keep?: { until: number | null };
  /** Between the machines and what it may do: the keys' block. */
  keys?: ReactNode;
  /** The person's keys guard something: machines under them say so. */
  locks: boolean;
  disabled?: boolean;
  /** The ids' prefix: two forms are never on one page, but a dialog over
   * Settings is. */
  idPrefix: string;
}

export function AiChoice(props: Props) {
  const t = useT();
  const { ai, projects, ticks, idPrefix: id } = props;
  const shown = projects.find((p) => p.id === props.project) ?? projects[0];
  const flip = (machine: string, which: 0 | 1) => {
    const pair: [boolean, boolean] = [...(ticks[machine] ?? [false, false])];
    pair[which] = !pair[which];
    props.onTicks({ ...ticks, [machine]: pair });
  };
  return (
    <div className="ai-choice">
      {projects.length > 1 && (
        <div className="ai-field">
          <label htmlFor={`${id}-project`}>{t("ai.project")}</label>
          <span className="settings-select ai-select">
            <select id={`${id}-project`} value={shown?.id ?? ""} disabled={props.disabled}
              onChange={(event) => props.onProject(event.target.value)}>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <ChevronDown size={15} />
          </span>
        </div>
      )}
      <div className="ai-field">
        <span id={`${id}-machines`} className="ai-field-name">
          {projects.length > 1 ? t("ai.machines") : t("ai.machines_of", { project: shown?.name ?? "" })}
        </span>
        <div className="ai-machines" role="group" aria-labelledby={`${id}-machines`}>
          <div className="ai-machines-head" aria-hidden="true">
            <span>{t("ai.machine")}</span>
            <span>{t("ai.terminals")}</span>
            <span>{t("ai.log")}</span>
          </div>
          {!shown?.machines.length && (
            <p className="ai-machines-none">{t("ai.no_machines")}</p>
          )}
          {shown?.machines.map((m) => (
            <div key={m.id} className="ai-machine">
              <span className="ai-machine-name">
                {m.kind === "computer" ? <Computer size={16} /> : <Server size={16} />}
                <span className="ai-machine-text">
                  <span className="ai-machine-title">{m.name}</span>
                  <span className="ai-machine-how">
                    {machineHow(m.kind, m.method, t)}
                    {m.guarded && props.locks && (
                      <span className="ai-lock"><Lock size={11} />{t("ai.under_keys")}</span>
                    )}
                  </span>
                </span>
              </span>
              <label className="ai-tick">
                <input type="checkbox" checked={!!ticks[m.id]?.[0]} disabled={props.disabled}
                  aria-label={t("ai.terminals_of", { machine: m.name })}
                  onChange={() => flip(m.id, 0)} />
              </label>
              <label className="ai-tick">
                <input type="checkbox" checked={!!ticks[m.id]?.[1]} disabled={props.disabled}
                  aria-label={t("ai.log_of", { machine: m.name })}
                  onChange={() => flip(m.id, 1)} />
              </label>
            </div>
          ))}
        </div>
        <p className="ai-small">{t("ai.machines_note")}</p>
      </div>
      {props.keys}
      <fieldset className="ai-can">
        <legend>{t("ai.can_title", { ai })}</legend>
        <div className="ai-can-item on">
          <Check size={16} />
          <span className="ai-can-text">
            <strong>{t("ai.can_see")}</strong>
            <span>{t("ai.can_see_text")}</span>
          </span>
        </div>
        <p className="ai-cannot"><Cross size={10} />{t("ai.cannot", { ai })}</p>
      </fieldset>
      <div className="ai-field">
        <label htmlFor={`${id}-term`}>{t("ai.term")}</label>
        <span className="settings-select ai-select">
          <select id={`${id}-term`} value={props.term} disabled={props.disabled}
            onChange={(event) => props.onTerm(event.target.value as ChoiceTerm)}>
            {props.keep && (
              <option value="keep">
                {t("ai.term_keep", { until: untilWords(props.keep.until, t) })}
              </option>
            )}
            {TERMS.map((term) => <option key={term} value={term}>{termOption(term, t)}</option>)}
          </select>
          <ChevronDown size={15} />
        </span>
        <span className="ai-small">
          {props.term === "forever" || (props.term === "keep" && props.keep?.until === null)
            ? t("ai.term_forever_note", { ai }) : t("ai.term_days_note", { ai })}
        </span>
      </div>
    </div>
  );
}

/** The vault password's proof, checked by tTerm: derived here, as the
 * page derives it to open the vault (vault/store.ts), and sent to tTerm,
 * which counts a wrong one as it counts them everywhere. */
async function passwordProof(password: string): Promise<string> {
  const vault = await loadVault();
  const settings = vault ? passwordSettings(vault.password) : null;
  if (!settings) throw new VaultError("vault_problem.no_password");
  const { kek, auth } = await passwordKeys(password, settings.salt, settings.kdf);
  wipe(kek);
  try {
    const proof = b64u(auth);
    await openVaultPassword(proof).catch((error: unknown) => {
      throw passwordWords(error);
    });
    return proof;
  } finally {
    wipe(auth);
  }
}

interface KeysProps {
  ai: string;
  /** The machines under the keys among those ticked. */
  locked: string[];
  /** Open on this device, a proof by password, or neither. */
  open: boolean;
  proof: string | null;
  /** The vault has a password to type here. */
  password: boolean;
  onProof: (proof: string) => void;
  /** Ask again whether the keys are open on this device. */
  onCheck?: () => void;
  idPrefix: string;
}

/** Machines under the keys, ticked: allowed with the keys open on this
 * device, or with the vault's password typed here; with neither, they are
 * left unticked. Either way the AI sees them only while the keys are open
 * on this device. */
export function AiKeys(props: KeysProps) {
  const t = useT();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  if (!props.locked.length) return null;
  const names = list(props.locked, t.lang);
  if (props.open || props.proof) {
    return (
      <p className="ai-keys-open" role="status">
        <Check size={16} />
        <span>{props.open ? t("ai.keys_open_here", { machines: names, ai: props.ai })
          : t("ai.keys_password_ok", { machines: names, ai: props.ai })}</span>
      </p>
    );
  }
  const open = async () => {
    setBusy(true);
    setProblem(null);
    try {
      props.onProof(await passwordProof(password));
      setPassword("");
    } catch (error) {
      setProblem(problemText(t, error, false));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="ai-keys" role="group" aria-labelledby={`${props.idPrefix}-keys`}>
      <p id={`${props.idPrefix}-keys`} className="ai-keys-title">
        <Lock size={15} /><span>{t("ai.keys_locked", { machines: names })}</span>
      </p>
      <p className="ai-keys-text">{t("ai.keys_text", { ai: props.ai })}</p>
      {props.password ? (
        <form className="ai-keys-form" onSubmit={(event) => {
          event.preventDefault();
          if (password && !busy) void open();
        }}>
          <label htmlFor={`${props.idPrefix}-pass`}>{t("ai.keys_password")}</label>
          <span className="ai-keys-row">
            <input id={`${props.idPrefix}-pass`} type="password" className="text-input"
              autoComplete="current-password" value={password} disabled={busy}
              onChange={(event) => setPassword(event.target.value)} />
            <button type="submit" className="button-accent" disabled={busy || !password}>
              {busy ? t("ai.keys_opening") : t("ai.keys_open")}
            </button>
          </span>
        </form>
      ) : (
        <span className="ai-keys-row">
          <span className="ai-keys-text">{t("ai.keys_no_password")}</span>
          {props.onCheck && (
            <button type="button" className="button-plain small" onClick={props.onCheck}>
              {t("ai.keys_check")}
            </button>
          )}
        </span>
      )}
      {problem && <div className="error-box" role="alert">{problem}</div>}
    </div>
  );
}
