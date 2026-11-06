// The words of AI through MCP (0.1.29) that the allow page, Settings › AI,
// the project's page and the terminal's chip share: what an AI may do, for
// how long, which machines, and how each machine opens.

import type { AiCan, AiMachine, LoginMethod, MachineKind } from "./api";
import { list, type Translate } from "./i18n";
import { agoLong, day } from "./time";

/** The terms the allow page offers, in its order. */
export const TERMS = ["forever", "30", "7", "1"] as const;
export type Term = (typeof TERMS)[number];

const DAY_MS = 86_400_000;

/** Until when a term goes, from now: null for until taken back. */
export function termUntil(term: Term, now: number = Date.now()): number | null {
  return term === "forever" ? null : now + Number(term) * DAY_MS;
}

/** A term as its option says it: "Until I take it back (no end)", "30
 * days, until 5 Dec". */
export function termOption(term: Term, t: Translate): string {
  if (term === "forever") return t("ai.term_forever");
  return t("ai.term_days", { n: Number(term), day: day(termUntil(term)!, t) });
}

/** "until you take it back", "until 5 Dec". */
export function untilWords(until: number | null, t: Translate): string {
  return until === null ? t("ai.until_forever") : t("ai.until_day", { day: day(until, t) });
}

/** How a machine opens, in a few words, under its name. */
export function machineHow(kind: MachineKind, method: LoginMethod | null | undefined,
  t: Translate): string {
  if (kind === "computer") return t("ai.how_computer");
  if (method === "key") return t("ai.how_key");
  if (method === "mac") return t("ai.how_mac");
  return t("ai.how_line");
}

/** What it may do, as chips: Looks, Proposes commands, Writes notes. */
export function canChips(can: AiCan, t: Translate): string[] {
  const out: string[] = [];
  if (can.see) out.push(t("ai.chip_see"));
  if (can.propose) out.push(t("ai.chip_propose"));
  if (can.notes) out.push(t("ai.chip_notes"));
  return out;
}

/** Its machines, a project at a time: "Personal: web-01, droplet; only
 * the log of db-internal". */
export function machinesLine(machines: AiMachine[], t: Translate): string {
  const projects: { name: string; items: AiMachine[] }[] = [];
  for (const m of machines) {
    const p = projects.find((x) => x.name === m.project);
    if (p) p.items.push(m);
    else projects.push({ name: m.project, items: [m] });
  }
  return projects.map((p) => {
    const both = p.items.filter((m) => m.terminals && m.log).map((m) => m.name);
    const log = p.items.filter((m) => !m.terminals && m.log).map((m) => m.name);
    const terminals = p.items.filter((m) => m.terminals && !m.log).map((m) => m.name);
    const parts: string[] = [];
    if (both.length) parts.push(list(both, t.lang));
    if (log.length) parts.push(t("ai.only_log", { machines: list(log, t.lang) }));
    if (terminals.length) parts.push(t("ai.only_terminals", { machines: list(terminals, t.lang) }));
    return t("ai.project_machines", { project: p.name, machines: parts.join("; ") });
  }).join(" · ");
}

/** "Until you take it back · last asked 2 hours ago". */
export function timesLine(until: number | null, lastCall: number | null, t: Translate): string {
  const term = untilWords(until, t);
  const first = term.charAt(0).toLocaleUpperCase(t.lang) + term.slice(1);
  return lastCall
    ? t("ai.times", { term: first, ago: agoLong(lastCall, t) })
    : t("ai.times_never", { term: first });
}

/** Where it takes the person back, after its name: "Claude · claude.ai";
 * an app on this computer by its own words. */
export function aiTitle(name: string, returnsTo: string, t: Translate): string {
  if (!returnsTo) return name;
  if (returnsTo === "127.0.0.1" || returnsTo === "localhost" || returnsTo === "::1") {
    return t("ai.title_here", { name });
  }
  if (returnsTo.endsWith(":")) return t("ai.title_app", { name });
  return t("ai.title_web", { name, place: returnsTo });
}
