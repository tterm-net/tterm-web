// Talking to the core. Every request that changes something carries the
// x-tterm header: the core refuses such requests without it, which is what
// keeps other sites in other tabs from acting on your behalf.

export type Theme = "system" | "light" | "dark";
export type Role = "owner" | "admin" | "member" | "observer";
export type MachineKind = "server" | "computer";

export interface Passport {
  os?: string;
  os_id?: string;
  os_version?: string;
  kernel?: string;
  arch?: string;
  hostname?: string;
  package_manager?: string;
  shell?: string;
  shells?: string[];
  cpus?: number;
  memory_kb?: number;
  disk_total_kb?: number;
  disk_free_kb?: number;
  sudo?: boolean;
  /** sudo is there but asks for a password, as on a person's own Mac. */
  sudo_password?: boolean;
  systemd?: boolean;
  commands_count?: number;
  checked_at?: string;
  /** A computer's agent version, and whose account it runs as. */
  agent?: string;
  user?: string;
}

/** A computer's agent while it is connected. */
export interface AgentInfo {
  version: string;
  /** Connected since, in milliseconds. */
  since: number;
}

export interface Machine {
  id: string;
  kind: MachineKind;
  name: string;
  host: string | null;
  os: string | null;
  passport?: Passport;
  /** A computer is online while its agent is connected. */
  online?: boolean;
  agent?: AgentInfo | null;
  /** When its agent was last connected, in milliseconds. */
  lastSeen?: number | null;
}

/** A machine taken out of tTerm Web. For a server, the command that takes
 * tTerm Web's access back on the server itself. */
export interface Removed {
  id: string;
  name: string;
  kind: MachineKind;
  /** For a server: the command that takes tTerm Web's access back there. */
  cleanup?: string | null;
  /** The same server is in another project too: its access stays. */
  shared?: boolean;
}

export interface Project {
  id: string;
  name: string;
  code: string;
  role: Role;
  machines: Machine[];
}

export interface Me {
  id: string;
  name: string;
  firstName: string;
  initials: string;
  theme: Theme;
  plan: string;
}

export type TerminalState = "connecting" | "open" | "ended" | "gone";

export interface TerminalInfo {
  id: string;
  machineId: string;
  machine: string;
  /** As tabs show it: P/web-01, or P/web-01 · 2 for the second one. */
  label: string;
  name: string;
  state: TerminalState;
  /** How it ended, in a sentence; null while it runs. */
  reason: string | null;
  /** Open, and its computer asleep or offline: it waits for it, and goes
   * on where it was when it is back. Since when, in milliseconds. */
  away?: boolean;
  awaySince?: number | null;
  shell?: string | null;
  /** Whether the shell prints our markers at its prompts. */
  markers?: boolean;
  /** Whether it marks where each command starts too: then commands become
   * blocks and tTerm's own input line works. */
  blocks?: boolean;
  cwd?: string;
  home?: string;
  branch?: string;
  venv?: string;
  /** The command running now, if any. */
  running?: RunningCommand | null;
}

export interface RunningCommand {
  command: string;
  cwd: string;
  branch: string;
  seqStart: number;
  cols: number;
  at: number;
}

/** A finished block as the database keeps it, with the end of its output. */
export interface StoredBlock {
  id: number;
  seqStart: number;
  seqEnd: number | null;
  command: string;
  cwd: string;
  branch: string;
  startedAt: number | null;
  finishedAt: number | null;
  exitCode: number | null;
  cols: number | null;
  /** The middle of a long output was not kept. */
  cut: boolean;
  /** Only the end of the output came along. */
  partial: boolean;
  size: number;
  /** Base64. */
  output: string;
}

export interface Feed {
  blocks: StoredBlock[];
  /** There are earlier blocks. */
  more: boolean;
}

export interface HistoryEntry {
  command: string;
  /** Run on this machine; otherwise elsewhere in the project. */
  here: boolean;
}

export interface CompleteItem {
  /** The whole word, quoted as it goes into the line. */
  text: string;
  label: string;
  kind: "dir" | "file" | "command" | "branch";
}

export interface Completion {
  start: number;
  end: number;
  quote: string;
  kind: string;
  items: CompleteItem[];
  more: boolean;
}

export interface DirList {
  cwd: string;
  home: string;
  dirs: string[];
}

export interface BranchList {
  current: string;
  local: string[];
  remote: string[];
}

export interface State {
  version: string;
  me: Me;
  projects: Project[];
  /** The terminals running now, the oldest first. */
  terminals: TerminalInfo[];
}

export interface Install {
  id: string;
  command: string;
  /** For a computer: the agent started by hand, for trying one not yet
   * released. */
  byHand?: string | null;
  expiresAt: string;
  minutes: number;
}

export type InstallState =
  | "waiting" | "connecting" | "connected" | "failed" | "expired";

export interface InstallStatus {
  state: InstallState;
  error: string | null;
  expiresAt: string;
  machine: { id: string; name: string; host: string | null;
    os: string | null; label: string } | null;
}

export class CoreError extends Error {}

async function check(response: Response): Promise<Response> {
  if (!response.ok) {
    let detail = "";
    try {
      detail = (await response.clone().json()).error ?? "";
    } catch {
      // Not JSON; the status says enough.
    }
    throw new CoreError(detail || `tTerm Web answered ${response.status}`);
  }
  return response;
}

function change(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "content-type": "application/json", "x-tterm": "1" },
    body: JSON.stringify(body),
  };
}

export async function loadState(): Promise<State> {
  const response = await fetch("/api/state", {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  return (await check(response)).json();
}

export async function saveTheme(theme: Theme): Promise<void> {
  await check(await fetch("/api/me", change("PATCH", { theme })));
}

export async function newInstall(projectId: string,
  kind: MachineKind = "server"): Promise<Install> {
  const response = await fetch("/api/installs",
    change("POST", { projectId, kind }));
  return (await check(response)).json();
}

export async function installStatus(id: string): Promise<InstallStatus> {
  const response = await fetch(`/api/installs/${id}`, { cache: "no-store" });
  return (await check(response)).json();
}

export async function openTerminal(machineId: string, cols: number,
  rows: number): Promise<TerminalInfo> {
  const response = await fetch("/api/terminals",
    change("POST", { machineId, cols, rows }));
  return (await check(response)).json();
}

export async function terminalInfo(id: string): Promise<TerminalInfo | null> {
  const response = await fetch(`/api/terminals/${id}`, { cache: "no-store" });
  if (response.status === 404) return null;
  return (await check(response)).json();
}

/** Ends the terminal: its shell gets a hangup, as when a window closes. */
export async function endTerminal(id: string): Promise<TerminalInfo | null> {
  const response = await fetch(`/api/terminals/${id}`,
    { method: "DELETE", headers: { "x-tterm": "1" } });
  if (response.status === 404) return null;
  return (await check(response)).json();
}

/** Takes a machine out of tTerm Web: its terminals end, a computer's agent
 * stops, the history stays. */
export async function removeMachine(id: string): Promise<Removed> {
  const response = await fetch(`/api/machines/${id}`,
    { method: "DELETE", headers: { "x-tterm": "1" } });
  return (await check(response)).json();
}

export async function loadFeed(id: string, before?: number,
  limit = 60): Promise<Feed | null> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (before !== undefined) query.set("before", String(before));
  const response = await fetch(`/api/terminals/${id}/blocks?${query}`,
    { cache: "no-store" });
  if (response.status === 404) return null;
  return (await check(response)).json();
}

export async function blockOutput(id: string,
  seqStart: number): Promise<Uint8Array | null> {
  const response = await fetch(`/api/terminals/${id}/blocks/${seqStart}/output`,
    { cache: "no-store" });
  if (response.status === 404) return null;
  return new Uint8Array(await (await check(response)).arrayBuffer());
}

export async function loadHistory(id: string): Promise<HistoryEntry[]> {
  const response = await fetch(`/api/terminals/${id}/history`,
    { cache: "no-store" });
  if (response.status === 404) return [];
  return (await (await check(response)).json()).commands;
}

export async function completeWord(id: string, line: string,
  cursor: number): Promise<Completion> {
  const response = await fetch(`/api/terminals/${id}/complete`,
    change("POST", { line, cursor }));
  return (await check(response)).json();
}

export async function listDirs(id: string): Promise<DirList> {
  const response = await fetch(`/api/terminals/${id}/dirs`, change("POST", {}));
  return (await check(response)).json();
}

export async function listBranches(id: string): Promise<BranchList> {
  const response = await fetch(`/api/terminals/${id}/branches`,
    change("POST", {}));
  return (await check(response)).json();
}
