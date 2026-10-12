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
  systemd?: boolean;
  commands_count?: number;
  checked_at?: string;
}

export interface Machine {
  id: string;
  kind: MachineKind;
  name: string;
  host: string | null;
  os: string | null;
  passport?: Passport;
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
  shell?: string | null;
  /** Whether the shell prints our markers, so commands become blocks. */
  markers?: boolean;
  cwd?: string;
  home?: string;
  branch?: string;
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
  expiresAt: string;
  minutes: number;
}

export type InstallState =
  | "waiting" | "connecting" | "connected" | "failed" | "expired";

export interface InstallStatus {
  state: InstallState;
  error: string | null;
  expiresAt: string;
  machine: { id: string; name: string; host: string; os: string | null;
    label: string } | null;
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

export async function newInstall(projectId: string): Promise<Install> {
  const response = await fetch("/api/installs",
    change("POST", { projectId, kind: "server" }));
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
