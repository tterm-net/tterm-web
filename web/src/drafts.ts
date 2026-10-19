// What is typed in a note and not saved yet, kept in this browser until it
// is: a tab closed without a connection, a page reloaded mid-save, a
// session that ended. Opening the note again brings it back (NoteView).
//
// Each open view of a note keeps a draft of its own, under the person's id,
// the note's and the view's: tterm.draft.<person>.<note>.<view>. A view
// writes and removes only its own, so two windows never undo each other's.
// One that opens the note takes over the drafts other views left behind.
// Sign out forgets them all, and so does another person signing in here.

import type { Block } from "./api";

const PREFIX = "tterm.draft.";

export interface Doc {
  title: string;
  blocks: Block[];
}

export interface Base extends Doc {
  version: number;
}

export interface Draft {
  /** The version the text was typed on. */
  base: Base;
  /** The text, title and blocks. */
  mine: Doc;
  /** What a save on its way when the draft was written sent: if it got
   * there, the text was typed on that. */
  sent: Doc | null;
  /** When it was written, in milliseconds. */
  at: number;
  /** Written by a view as it closed, or as the page unloaded: left for the
   * next one. A view still open somewhere keeps its draft to itself. */
  closed: boolean;
}

/** A draft not written for this long has no view looking after it: its
 * window crashed, say. */
const ABANDONED_MS = 60_000;

/** Off between Sign out and the next sign-in: views closing then keep
 * nothing. */
let off = false;
/** Notes deleted from this page: their views keep no draft as they close. */
const deleted = new Set<string>();

export function markDeleted(noteId: string): void {
  deleted.add(noteId);
}

export function isDeleted(noteId: string): boolean {
  return deleted.has(noteId);
}

export function draftKey(personId: string, noteId: string, viewId: string): string {
  return `${PREFIX}${personId}.${noteId}.${viewId}`;
}

function isBlocks(value: unknown): value is Block[] {
  return Array.isArray(value) && value.every((b) => !!b && typeof b === "object" &&
    typeof (b as Block).id === "string" && typeof (b as Block).type === "string" &&
    typeof (b as Block).text === "string");
}

function isDoc(value: unknown): value is Doc {
  const doc = value as Doc | null;
  return !!doc && typeof doc.title === "string" && isBlocks(doc.blocks);
}

function parse(raw: string | null): Draft | null {
  try {
    const d = JSON.parse(raw ?? "null");
    if (d && isDoc(d.base) && typeof d.base.version === "number" && isDoc(d.mine) &&
      (d.sent === null || d.sent === undefined || isDoc(d.sent))) {
      return { base: d.base, mine: d.mine, sent: d.sent ?? null,
        at: typeof d.at === "number" ? d.at : 0, closed: d.closed === true };
    }
  } catch {
    // Not readable: no draft.
  }
  return null;
}

export function writeDraft(key: string, draft: Draft | null): void {
  if (off && draft) return;
  try {
    if (draft) localStorage.setItem(key, JSON.stringify(draft));
    else localStorage.removeItem(key);
  } catch {
    // Storage full or off: the note is then only as safe as its saves.
  }
}

/** The drafts of the note that other views left behind, the newest first:
 * not those of a view still open in another window. */
export function leftDrafts(personId: string, noteId: string,
  own: string): { key: string; draft: Draft }[] {
  const prefix = `${PREFIX}${personId}.${noteId}.`;
  const found: { key: string; draft: Draft }[] = [];
  try {
    for (const key of Object.keys(localStorage)) {
      if (!key.startsWith(prefix) || key === own) continue;
      const draft = parse(localStorage.getItem(key));
      if (!draft) localStorage.removeItem(key);
      else if (draft.closed || Date.now() - draft.at > ABANDONED_MS) {
        found.push({ key, draft });
      }
    }
  } catch {
    // No storage: no drafts.
  }
  return found.sort((a, b) => b.draft.at - a.draft.at);
}

/** Every draft of the note, of any view: it is deleted. */
export function dropDrafts(personId: string, noteId: string): void {
  const prefix = `${PREFIX}${personId}.${noteId}.`;
  try {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith(prefix)) localStorage.removeItem(key);
    }
  } catch {
    // Nothing to drop.
  }
}

/** Sign out: this browser forgets the person's drafts, and views that
 * close now keep none. */
export function forgetDrafts(): void {
  off = true;
  try {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith(PREFIX)) localStorage.removeItem(key);
    }
  } catch {
    // Nothing to forget.
  }
}

/** Signed in as this person: drafts of anyone else go, and drafts are
 * kept again. */
export function draftsOf(personId: string): void {
  off = false;
  try {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith(PREFIX) && !key.startsWith(`${PREFIX}${personId}.`)) {
        localStorage.removeItem(key);
      }
    }
  } catch {
    // Nothing to sort out.
  }
}

/** Whether drafts are off now (signed out). */
export function draftsOff(): boolean {
  return off;
}
