// Talking to the core. Every request that changes something carries the
// x-tterm header: the core refuses such requests without it, which is what
// keeps other sites in other tabs from acting on your behalf.

export type Theme = "system" | "light" | "dark";
export type Role = "owner" | "admin" | "member" | "observer";

export interface Machine {
  id: string;
  kind: "server" | "computer";
  name: string;
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

export interface State {
  version: string;
  me: Me;
  projects: Project[];
}

export class CoreError extends Error {}

async function check(response: Response): Promise<Response> {
  if (!response.ok) {
    throw new CoreError(`tTerm Web answered ${response.status}`);
  }
  return response;
}

export async function loadState(): Promise<State> {
  const response = await fetch("/api/state", {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  return (await check(response)).json();
}

export async function saveTheme(theme: Theme): Promise<void> {
  await check(
    await fetch("/api/me", {
      method: "PATCH",
      headers: { "content-type": "application/json", "x-tterm": "1" },
      body: JSON.stringify({ theme }),
    }),
  );
}
