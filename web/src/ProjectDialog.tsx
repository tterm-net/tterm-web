// The windows of projects of one's own (0.1.1), as on the canvas: a new
// project and the pencil, with the name, the code and the color and how
// they will look; moving a machine to another project; deleting a project.

import {
  useEffect, useRef, useState, type FormEvent, type KeyboardEvent as KeyEvent,
  type ReactNode,
} from "react";
import {
  PROJECT_COLORS, type Machine, type Project, type ProjectColor,
  type ProjectFields,
} from "./api";
import {
  COLOR_NAMES, colorClass, freeColor, freeName, suggestCode, validCode,
} from "./colors";
import { Check, Close, Server } from "./icons";
import { listed, many } from "./rights";

/** Esc closes the window, whatever has the keys inside it. */
function useEscape(onClose: () => void) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

const STEPS: Record<string, number> = {
  ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1,
};

/** The arrow keys move the choice in a group of radio buttons, as in any
 * other such group; Tab stops only at the chosen one. */
function radioKeys<T>(values: readonly T[], value: T, choose: (v: T) => void) {
  return (event: KeyEvent<HTMLElement>) => {
    const step = STEPS[event.key];
    if (!step || !values.length) return;
    event.preventDefault();
    const next = (Math.max(values.indexOf(value), 0) + step + values.length) %
      values.length;
    choose(values[next]);
    event.currentTarget.querySelectorAll<HTMLElement>('[role="radio"]')[next]?.focus();
  };
}

/** Enter and Esc while a word is being composed (Japanese, Chinese) are
 * the input method's. */
function composing(event: KeyEvent): boolean {
  return event.nativeEvent.isComposing || event.keyCode === 229;
}

function Window(props: {
  id: string;
  title: string;
  sub?: string;
  className?: string;
  role?: "dialog" | "alertdialog";
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className="scrim">
      <section className={`dialog ${props.className ?? ""}`} role={props.role ?? "dialog"}
        aria-modal="true" aria-labelledby={props.id}>
        <div className="dialog-head">
          <div className="dialog-title">
            <h2 id={props.id}>{props.title}</h2>
            {props.sub && <p className="dialog-note">{props.sub}</p>}
          </div>
          <button type="button" className="icon-button small" aria-label="Close"
            onClick={props.onClose}>
            <Close size={16} />
          </button>
        </div>
        {props.children}
      </section>
    </div>
  );
}

/** A new project, or the pencil on a project's page. */
export function ProjectDialog(props: {
  /** The project to change; none for a new one. */
  project?: Project;
  /** All of the person's projects: the codes and colors already theirs. */
  projects: Project[];
  onSave: (fields: ProjectFields) => Promise<void>;
  onCancel: () => void;
}) {
  const { project } = props;
  const editing = !!project;
  const own = props.projects.filter((p) => p.own && p.id !== project?.id);
  const taken = own.map((p) => p.code);
  const [name, setName] = useState(project?.name ?? "");
  const [code, setCode] = useState(project?.code ?? "");
  /** The code typed by hand: from then on it no longer follows the name. */
  const [codeTyped, setCodeTyped] = useState(editing);
  const [color, setColor] = useState<ProjectColor>(project?.color ??
    freeColor(props.projects));
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  useEscape(props.onCancel);

  useEffect(() => {
    nameInput.current?.focus();
    if (editing) nameInput.current?.select();
  }, [editing]);

  const shownCode = codeTyped ? code : suggestCode(name, taken);
  const clean = name.trim();
  const ready = !!clean && validCode(shownCode) && !busy;
  const alone = !project || project.personal || project.alone;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready) {
      if (clean && !validCode(shownCode)) {
        setProblem("The code is 1 to 4 letters or digits.");
      }
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      await props.onSave({ name: clean, code: shownCode, color });
    } catch (e) {
      setProblem((e as Error).message || "tTerm Web did not answer.");
      setBusy(false);
    }
  };

  return (
    <Window id="pd-title" className="project-dialog"
      title={editing ? "Edit project" : "New project"}
      sub={editing ? (alone ? undefined : "The owner and admins change these.")
        : "A project keeps its own machines and notes, apart from your other projects."}
      onClose={props.onCancel}>
      <form className="project-form" onSubmit={(e) => void submit(e)}>
        <div className="pd-fields">
          <div className="pd-field">
            <label htmlFor="pd-name">Name</label>
            <input id="pd-name" ref={nameInput} className="text-input" value={name}
              maxLength={80} autoComplete="off" spellCheck={false}
              placeholder="For example, Client X"
              onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="pd-field code">
            <label htmlFor="pd-code">Short code</label>
            <input id="pd-code" className="text-input" value={shownCode}
              maxLength={4} autoComplete="off" spellCheck={false}
              onChange={(e) => {
                setCodeTyped(true);
                setCode(e.target.value.toUpperCase().replace(/\s/g, ""));
              }} />
          </div>
        </div>
        <p className="pd-help">
          {editing
            ? `1 to 4 letters or digits. The code marks the project in tabs and in the sidebar${alone ? "." : ", for everyone in it at once."}`
            : "The code is made from the name, and you can change it: 1 to 4 " +
              "letters or digits. It marks the project in tabs and in the sidebar."}
        </p>
        <div className="pd-colors">
          <span className="pd-label" id="pd-color">Color</span>
          <div className="swatches" role="radiogroup" aria-labelledby="pd-color"
            onKeyDown={radioKeys(PROJECT_COLORS, color, setColor)}>
            {PROJECT_COLORS.map((c) => (
              <button key={c} type="button" role="radio" aria-checked={c === color}
                tabIndex={c === color ? 0 : -1}
                className={`swatch ${colorClass(c)}`} aria-label={COLOR_NAMES[c]}
                title={COLOR_NAMES[c]} onClick={() => setColor(c)}>
                {c === color && <Check size={15} />}
              </button>
            ))}
          </div>
          <p className="pd-help">
            {!editing
              ? "The color is picked to differ from your other projects, and you " +
                "can change it. It fills the code badge, and the whole project " +
                "in the sidebar if you choose Whole project on your account page."
              : alone
              ? "The color fills the code badge, and the whole project in the " +
                "sidebar if you choose Whole project on your account page."
              : "The color fills the code badge for everyone in it, and the whole " +
                "project in the sidebar for those who choose Whole project on " +
                "their account page."}
          </p>
        </div>
        <div className={`pd-preview ${colorClass(color)}`} aria-hidden="true">
          <span className="pd-preview-label">Preview</span>
          <span className="pd-preview-project">
            <span className="code-badge">{shownCode || "?"}</span>
            {clean || "Project"}
          </span>
          <span className="pd-preview-tab">
            <span className="dot open" />
            <span><span className="tab-code">{shownCode || "?"}/</span>web-01</span>
          </span>
        </div>
        {!editing && (
          <div className="pd-note">
            <Server size={16} />
            <span>You own it. Next you add a machine or move one in from another
              project, and write notes.</span>
          </div>
        )}
        {problem && <div className="error-box" role="alert">{problem}</div>}
        <div className="button-row end">
          <button type="button" className="button-plain" onClick={props.onCancel}>
            Cancel
          </button>
          <button type="submit" className="button-accent big" disabled={!ready}>
            {editing ? "Save" : "Create project"}
          </button>
        </div>
      </form>
    </Window>
  );
}

/** A machine moved to another project of one's own. */
export function MoveDialog(props: {
  machine: Machine;
  /** The projects it may go to: the person's own, but its own. */
  targets: Project[];
  onMove: (projectId: string) => Promise<void>;
  onCancel: () => void;
}) {
  const { machine, targets } = props;
  const [to, setTo] = useState(targets[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const group = useRef<HTMLDivElement>(null);
  useEscape(props.onCancel);
  useEffect(() => {
    group.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
  }, []);
  const target = targets.find((p) => p.id === to);
  const name = target
    ? freeName(machine.name, target.machines.map((m) => m.name)) : machine.name;

  const move = async () => {
    if (!target || busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await props.onMove(target.id);
    } catch (e) {
      setProblem((e as Error).message || "tTerm Web did not answer.");
      setBusy(false);
    }
  };

  return (
    <Window id="mv-title" className="confirm-dialog move-dialog"
      title={`Move ${machine.name}`} sub="To another project you own."
      onClose={props.onCancel}>
      <div className="move-targets" role="radiogroup" aria-labelledby="mv-title"
        ref={group} onKeyDown={radioKeys(targets.map((p) => p.id), to, setTo)}>
        {targets.map((p) => (
          <button key={p.id} type="button" role="radio" aria-checked={p.id === to}
            tabIndex={p.id === to ? 0 : -1}
            className={`move-target ${colorClass(p.color)}`} onClick={() => setTo(p.id)}>
            <span className="move-radio" aria-hidden="true" />
            <span className="code-badge" aria-hidden="true">{p.code}</span>
            <span className="move-name">{p.name}</span>
            <span className="move-sub">{p.alone || p.personal ? "only you"
              : "with others"}</span>
          </button>
        ))}
      </div>
      {target && (
        <div className="pd-note">
          <Server size={16} />
          <span>In {target.name} it becomes {target.code}/{name}
            {name !== machine.name ? `: the name ${machine.name} is taken there` : ""}.
            Its terminals keep running, and nothing changes on the machine
            itself.</span>
        </div>
      )}
      {problem && <div className="error-box" role="alert">{problem}</div>}
      <div className="button-row end">
        <button type="button" className="button-plain" onClick={props.onCancel}>
          Cancel
        </button>
        <button type="button" className="button-accent big" disabled={!target || busy}
          onClick={() => void move()}>
          {target ? `Move to ${target.name}` : "Move"}
        </button>
      </div>
    </Window>
  );
}

/** Deleting a project: one with machines in it says what to do first; an
 * empty one asks for its name, since its notes go with it. */
export function DeleteProjectDialog(props: {
  project: Project;
  onDelete: () => Promise<void>;
  onCancel: () => void;
}) {
  const { project } = props;
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  useEscape(props.onCancel);
  useEffect(() => (input.current ?? close.current)?.focus(), []);
  const machines = project.machines.map((m) => m.name);
  const notes = project.folders.reduce((n, f) => n + f.notes.length, 0);
  const matches = typed.trim() === project.name;

  const go = async () => {
    if (!matches || busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await props.onDelete();
    } catch (e) {
      setProblem((e as Error).message || "tTerm Web did not answer.");
      setBusy(false);
    }
  };

  if (machines.length) {
    return (
      <Window id="dp-title" className="confirm-dialog" role="alertdialog"
        title={`Delete ${project.name}?`} onClose={props.onCancel}>
        <div className="confirm-body">
          <p className="ink">
            Move or remove its {machines.length === 1 ? "machine" : "machines"} first: {listed(machines)}.
          </p>
          <p>
            On the project's page, ⋯ next to a machine: Move to another project,
            or Remove machine. Then the project can go.
          </p>
        </div>
        <div className="button-row">
          <button type="button" className="button-plain" ref={close}
            onClick={props.onCancel}>
            Close
          </button>
        </div>
      </Window>
    );
  }

  return (
    <Window id="dp-title" className="confirm-dialog" role="alertdialog"
      title={`Delete ${project.name}?`} onClose={props.onCancel}>
      <div className="confirm-body">
        <p className="ink">This can't be undone.</p>
        <p>
          {project.folders.length
            ? `${many(project.folders.length, "folder", "folders")} with ${many(notes, "note", "notes")} ${project.folders.length === 1 ? "is" : "are"} deleted with it.`
            : "It has no notes."}
        </p>
        <label htmlFor="dp-name">Type {project.name} to confirm</label>
        <input id="dp-name" ref={input} className="text-input" value={typed}
          autoComplete="off" spellCheck={false} placeholder={project.name}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !composing(e)) void go();
          }} />
      </div>
      {problem && <div className="error-box" role="alert">{problem}</div>}
      <div className="button-row">
        <button type="button" className="button-danger big" disabled={!matches || busy}
          onClick={() => void go()}>
          Delete {project.name}
        </button>
        <button type="button" className="button-plain" onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </Window>
  );
}
