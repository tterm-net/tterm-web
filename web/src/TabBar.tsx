// The one thin line on top, as approved on 11.10: the sidebar's ⟩, the tabs,
// "+" for a terminal on any machine, and "Jump to" for the palette. From
// 0.1.0 a tab is a terminal, a note or a project's page, from any project:
// each starts with what it is and the project's code, faint.
//
// A terminal tab's dot is its machine: green on line, a turning ring while a
// command runs, a hollow ring while the computer sleeps, grey once it ended.
// A note says on the right whether what was typed is saved.

import { useRef, type ReactNode } from "react";
import { Check, Close, Doc, ListCheck, Plus, Search } from "./icons";
import { JUMP_KEYS, type TabKind } from "./tabs";

export type Dot = "open" | "busy" | "away" | "ended";

export interface TabView {
  id: string;
  kind: TabKind;
  /** The project's code. */
  code: string;
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
          return (
            <div key={tab.id} className={current ? "tab current" : "tab"}>
              <button type="button" className="tab-main"
                aria-current={current ? "page" : undefined} title={tab.title}
                onClick={() => props.onSelect(tab.id)}
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
