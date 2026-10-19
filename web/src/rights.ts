// Who may do what with a project's machines and notes, as the core decides
// it (core/notes.py): the page only hides what would be refused anyway.

import type { Folder, NoteSummary, Project } from "./api";

/** The owner and admins: machines, and folders with notes in them. */
export function manages(project: Project): boolean {
  return project.role === "owner" || project.role === "admin";
}

/** Nobody else in it: Personal, and, until people come, every project of
 * one's own (0.1.1). */
export function alone(project: Project): boolean {
  return project.personal || project.alone;
}

/** Everyone but an observer writes notes. */
export function writes(project: Project): boolean {
  return project.role !== "observer";
}

/** Its author, the owner and admins; an observer, even its author, no. */
export function deletesNote(project: Project, note: NoteSummary,
  meId: string): boolean {
  return manages(project) || (writes(project) && note.createdBy?.id === meId);
}

/** The owner and admins, and whoever made it while it is empty. */
export function deletesFolder(project: Project, folder: Folder,
  meId: string): boolean {
  return manages(project) ||
    (writes(project) && folder.createdBy?.id === meId && folder.notes.length === 0);
}

/** "2 notes", "1 machine". */
export function many(n: number, one: string, more: string): string {
  return `${n} ${n === 1 ? one : more}`;
}

/** "A, B and C". */
export function listed(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}
