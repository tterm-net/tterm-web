// The one thin line on top, as approved on 11.10: the sidebar's ⟩, the tabs,
// "+" for a terminal on any machine, and "Jump to" for the palette. From
// 0.1.0 a tab is a terminal, a note or a project's page, from any project:
// each starts with what it is and the project's code, faint.
//
// A terminal tab's dot is its machine: green on line, a turning ring while a
// command runs, a hollow ring while the computer sleeps, grey once it ended.
// A note says on the right whether what was typed is saved. From 0.1.1 the
// project's code is in its color, and a double click on a terminal's tab
// names the terminal: Enter saves, Esc cancels.

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ProjectColor } from "./api";
import { colorClass } from "./colors";
import { Check, Close, Doc, ListCheck, Plus, Search } from "./icons";
import { JUMP_KEYS, type TabKind } from "./tabs";

export type Dot = "open" | "busy" | "away" | "ended";

export interface TabView {
  id: string;
  kind: TabKind;
  /** The project's code, and its color. */
  code: string;
  color?: ProjectColor;
  /** What follows the code: web-01 · 2, a note's title, a project's name. */
  label: string;
  /** A terminal's state. */
  dot?: Dot;
  /** A note with a checklist in it. */
  checklist?: boolean;
  /** A note with no title yet. */
  untitled?: boolean;
  /** What the dot means, and where the tab's terminal or note is. */
  title: string;
  closeTitle: string;
  /** A terminal still running: its machine, its name, and whether a double
   * click names it. */
  machine?: string;
  name?: string;
  canRename?: boolean;
}

/** How saving goes in the note in front. */
export interface SaveLine {
  text: string;
  kind: "saved" | "saving" | "warn";
}

interface Props {
  sidebarButton: ReactNode;
  tabs: TabView[];
  current: string | null;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  /** The "+" list, while it is open. */
  picking: boolean;
  onPlus: () => void;
  renderPicker: (around: React.RefObject<HTMLElement | null>) => ReactNode;
  onJump: () => void;
  saving: SaveLine | null;
  /** The terminal's tab being named, if one is. */
  renaming: string | null;
  onStartRename: (id: string) => void;
  onRename: (id: string, name: string) => void;
  onStopRename: (id: string) => void;
}

/** The name being typed into a terminal's tab. Enter or leaving it saves,
 * Esc goes back to what it was. A number stays a placeholder. */
function RenameInput(props: {
  tab: TabView;
  onRename: (id: string, name: string) => void;
  onStop: () => void;
}) {
  const { tab } = props;
  const numbered = /^\d+$/.test(tab.name ?? "");
  const [value, setValue] = useState(numbered ? "" : tab.name ?? "");
  const input = useRef<HTMLInputElement>(null);
  const done = useRef(false);

  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);

  const commit = () => {
    if (done.current) return;
    done.current = true;
    const name = value.trim();
    if (!name || name === tab.name) props.onStop();
    else props.onRename(tab.id, name);
  };

  return (
    <input ref={input} className="tab-rename" aria-label="Terminal name"
      value={value} maxLength={40} autoComplete="off" spellCheck={false}
      placeholder={numbered ? `terminal ${tab.name}` : undefined}
      aria-describedby={`${tab.id}-tip`}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        // Enter and Esc while a word is being composed (Japanese, Chinese)
        // are the input method's: they take or drop the word.
        if (e.nativeEvent.isComposing || e.keyCode === 229) return;
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
        } else if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          done.current = true;
          props.onStop();
        }
      }}
      onBlur={commit} />
  );
}

function Lead({ tab }: { tab: TabView }) {
  if (tab.kind === "terminal") {
    return <span className={`dot ${tab.dot ?? "busy"}`} aria-hidden="true" />;
  }
  if (tab.kind === "note") {
    return tab.checklist ? <ListCheck size={14} className="tab-icon" />
      : <Doc size={14} className="tab-icon" />;
  }
  return <span className="code-chip" aria-hidden="true">{tab.code}</span>;
}

export function TabBar(props: Props) {
  const plus = useRef<HTMLDivElement>(null);
  return (
    <header className="topbar tab-bar">
      {props.sidebarButton}
      <nav className="tabs" aria-label="Open tabs">
        {props.tabs.map((tab) => {
          const current = tab.id === props.current;
          const renaming = props.renaming === tab.id && !!tab.canRename;
          return (
            <div key={tab.id}
              className={`${current ? "tab current" : "tab"} ${colorClass(tab.color)}`}>
              {renaming ? (
                <div className="tab-main renaming">
                  <Lead tab={tab} />
                  <span className="tab-label">
                    {tab.code && <span className="tab-code">{tab.code}/</span>}
                    {tab.machine} ·{" "}
                  </span>
                  <RenameInput tab={tab} onRename={props.onRename}
                    onStop={() => props.onStopRename(tab.id)} />
                </div>
              ) : (
                <button type="button" className="tab-main"
                  aria-current={current ? "page" : undefined} title={tab.title}
                  onClick={() => props.onSelect(tab.id)}
                  onDoubleClick={() => {
                    if (tab.canRename) props.onStartRename(tab.id);
                  }}
                  onAuxClick={(e) => {
                    if (e.button === 1) {
                      e.preventDefault();
                      props.onClose(tab.id);
                    }
                  }}>
                  <Lead tab={tab} />
                  <span className={tab.untitled ? "tab-label untitled" : "tab-label"}>
                    {tab.kind !== "project" && tab.code && (
                      <span className="tab-code">{tab.code}/</span>
                    )}
                    {tab.label}
                  </span>
                </button>
              )}
              {renaming && (
                <div className="tab-tip" role="tooltip" id={`${tab.id}-tip`}>
                  <strong>Name this terminal</strong>
                  <span>Enter saves, Esc cancels. The name shows in the sidebar,
                    in tabs and on the project's page.</span>
                </div>
              )}
              <button type="button" className="tab-close"
                aria-label={`Close the tab ${tab.code ? `${tab.code}/` : ""}${tab.label}`}
                title={tab.closeTitle}
                onClick={() => props.onClose(tab.id)}>
                <Close size={13} />
              </button>
            </div>
          );
        })}
        <div className="plus-wrap" ref={plus}>
          <button type="button" className="icon-button small"
            aria-label="New terminal" aria-haspopup="dialog"
            aria-expanded={props.picking}
            title="A new terminal on any machine" onClick={props.onPlus}>
            <Plus size={16} />
          </button>
          {props.picking && props.renderPicker(plus)}
        </div>
      </nav>
      <div className="bar-right">
        {props.saving && (
          <span className={`save-line ${props.saving.kind}`} role="status">
            {props.saving.kind === "saved" && <Check size={13} />}
            {props.saving.text}
          </span>
        )}
        <button type="button" className="jump" onClick={props.onJump}
          title="Go to any terminal, note or machine, or do anything here">
          <Search size={14} />
          <span className="jump-text">Jump to</span>
          <kbd>{JUMP_KEYS}</kbd>
        </button>
      </div>
    </header>
  );
}
