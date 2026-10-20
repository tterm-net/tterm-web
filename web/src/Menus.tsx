// The small menus behind ⋯ (a machine, a folder, a note), and the window
// that names a folder. A menu opens under its button and closes on a
// choice, a click elsewhere, or Esc; the arrow keys walk its items.

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import type { Folder } from "./api";
import {
  Close, Copy, Dots, Folder as FolderIcon, Layers, Lock, Pencil, Trash,
} from "./icons";

export function MenuButton(props: {
  label: string;
  title?: string;
  className?: string;
  /** What the menu holds; `close` closes it. */
  children: (close: () => void) => ReactNode;
  /** The menu opens to the left of the button's right edge (default), or
   * from its left edge. */
  align?: "right" | "left";
  /** Opened on the first render: a board in the mockups, a check. */
  startOpen?: boolean;
}) {
  const [open, setOpen] = useState(!!props.startOpen);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        box.current?.querySelector<HTMLButtonElement>(".menu-button")?.focus();
        return;
      }
      // The arrows are the menu's only while the keys are in it.
      if (!box.current?.contains(document.activeElement)) return;
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      const items = [...(box.current?.querySelectorAll<HTMLButtonElement>(
        ".menu [role^='menuitem']:not(:disabled)") ?? [])];
      if (!items.length) return;
      event.preventDefault();
      const at = items.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === "ArrowDown" ? (at + 1) % items.length
        : (at - 1 + items.length) % items.length;
      items[next].focus();
    };
    // Tab past its last item: the keys left it, and it closes.
    const left = (event: FocusEvent) => {
      const next = event.relatedTarget as Node | null;
      if (next && !box.current?.contains(next)) setOpen(false);
    };
    const anchor = box.current;
    document.addEventListener("mousedown", away);
    window.addEventListener("keydown", keys, true);
    anchor?.addEventListener("focusout", left);
    return () => {
      document.removeEventListener("mousedown", away);
      window.removeEventListener("keydown", keys, true);
      anchor?.removeEventListener("focusout", left);
    };
  }, [open]);

  return (
    <div className={`menu-anchor ${props.className ?? ""}`} ref={box}>
      <button type="button"
        className={open ? "icon-button small menu-button open" : "icon-button small menu-button"}
        aria-label={props.label} title={props.title ?? props.label}
        aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen((was) => !was)}>
        <Dots size={16} />
      </button>
      {open && (
        <div className={props.align === "left" ? "menu left" : "menu"} role="menu">
          {props.children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

/** A note's menu: the folder it is in, to move it to another; from 0.1.2
 * another project, for whoever may delete it; Copy as Markdown; Delete, or
 * who may delete it. */
export function NoteMenuItems(props: {
  folders: Folder[];
  folderId: string;
  canWrite: boolean;
  canDelete: boolean;
  /** There is another project the person writes notes in. */
  elsewhere: boolean;
  /** Who may delete it, when this person may not. */
  author: string;
  close: () => void;
  onMove: (folderId: string) => void;
  onMoveOut: () => void;
  onCopy: () => void;
  onDelete: () => void;
}) {
  const { close } = props;
  return (
    <>
      {props.canWrite && props.folders.length > 1 && (
        <>
          <div className="menu-label">Folder</div>
          {props.folders.map((f) => (
            <button key={f.id} type="button" className="menu-item"
              role="menuitemradio" aria-checked={f.id === props.folderId}
              onClick={() => {
                close();
                if (f.id !== props.folderId) props.onMove(f.id);
              }}>
              <span className="menu-radio" aria-hidden="true" />
              <FolderIcon size={14} />
              <span className="menu-text">{f.name}</span>
              <span className="menu-count">{f.notes.length}</span>
            </button>
          ))}
          <div className="menu-line" />
        </>
      )}
      {props.canDelete && props.elsewhere && (
        <button type="button" className="menu-item" role="menuitem"
          onClick={() => {
            close();
            props.onMoveOut();
          }}>
          <Layers size={15} />
          <span className="menu-text">Move to another project…</span>
        </button>
      )}
      <button type="button" className="menu-item" role="menuitem"
        onClick={() => {
          close();
          props.onCopy();
        }}>
        <Copy size={15} />
        <span className="menu-text">Copy as Markdown</span>
      </button>
      {props.canDelete ? (
        <button type="button" className="menu-item danger" role="menuitem"
          onClick={() => {
            close();
            props.onDelete();
          }}>
          <Trash size={15} />
          <span className="menu-text">Delete note</span>
        </button>
      ) : props.canWrite && (
        <div className="menu-note">
          <Lock size={14} />
          <span>{props.author}, the owner and admins can {props.elsewhere
            ? "move it to another project or delete it" : "delete it"}</span>
        </div>
      )}
    </>
  );
}

/** A folder's menu: Rename, and Delete or who may. */
export function FolderMenuItems(props: {
  canWrite: boolean;
  canDelete: boolean;
  close: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  const { close } = props;
  return (
    <>
      {props.canWrite && (
        <button type="button" className="menu-item" role="menuitem"
          onClick={() => {
            close();
            props.onRename();
          }}>
          <Pencil size={15} />
          <span className="menu-text">Rename folder</span>
        </button>
      )}
      {props.canDelete ? (
        <button type="button" className="menu-item danger" role="menuitem"
          onClick={() => {
            close();
            props.onDelete();
          }}>
          <Trash size={15} />
          <span className="menu-text">Delete folder</span>
        </button>
      ) : (
        <div className="menu-note">
          <Lock size={14} />
          <span>The owner and admins delete a folder with notes in it</span>
        </div>
      )}
    </>
  );
}

/** The window that names a folder: a new one, or one renamed. */
export function FolderDialog(props: {
  title: string;
  initial: string;
  /** The button's words: Create folder, Save. */
  action: string;
  help: string;
  onSave: (name: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [name, setName] = useState(props.initial);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const cancel = useRef(props.onCancel);
  cancel.current = props.onCancel;

  useEffect(() => {
    input.current?.focus();
    input.current?.select();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        cancel.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await props.onSave(name.trim());
    } catch (e) {
      setProblem((e as Error).message || "tTerm Web did not answer.");
      setBusy(false);
    }
  };

  return (
    <div className="scrim">
      <section className="dialog folder-dialog" role="dialog" aria-modal="true"
        aria-labelledby="fd-title">
        <div className="dialog-head">
          <div className="dialog-title">
            <h2 id="fd-title">{props.title}</h2>
          </div>
          <button type="button" className="icon-button small" aria-label="Close"
            onClick={props.onCancel}>
            <Close size={16} />
          </button>
        </div>
        <form className="folder-form" onSubmit={(e) => void submit(e)}>
          <label htmlFor="fd-name">Name</label>
          <input id="fd-name" ref={input} className="text-input" value={name}
            maxLength={80} autoComplete="off" spellCheck={false}
            placeholder="For example, Runbooks"
            onChange={(e) => setName(e.target.value)} />
          <p className="hint">{props.help}</p>
          {problem && <div className="error-box" role="alert">{problem}</div>}
          <div className="button-row end">
            <button type="button" className="button-plain" onClick={props.onCancel}>
              Cancel
            </button>
            <button type="submit" className="button-accent big"
              disabled={busy || !name.trim()}>
              {props.action}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
