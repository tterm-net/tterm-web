// The one thin line on top, as approved on 11.10: the sidebar's ⟩, the tabs
// of open terminals from any project, "+" for a terminal on any machine, and
// "Jump to" for the palette.
//
// A tab's dot is its machine: green on line, a turning ring while a command
// runs, a hollow ring while the computer sleeps, grey once it ended.

import { useRef, type ReactNode } from "react";
import { Close, Plus, Search } from "./icons";
import { JUMP_KEYS } from "./tabs";

export type Dot = "open" | "busy" | "away" | "ended";

export interface TabView {
  id: string;
  label: string;
  dot: Dot;
  /** What the dot means, and where the terminal is. */
  title: string;
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
}

export function TabBar(props: Props) {
  const plus = useRef<HTMLDivElement>(null);
  return (
    <header className="topbar tab-bar">
      {props.sidebarButton}
      <nav className="tabs" aria-label="Open terminals">
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
                <span className={`dot ${tab.dot}`} aria-hidden="true" />
                <span className="tab-label">{tab.label}</span>
              </button>
              <button type="button" className="tab-close"
                aria-label={`Close the tab ${tab.label}`}
                title="Close the tab. The terminal keeps running, in the sidebar under its machine."
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
        <button type="button" className="jump" onClick={props.onJump}
          title="Go to any terminal or machine, or do anything here">
          <Search size={14} />
          <span className="jump-text">Jump to</span>
          <kbd>{JUMP_KEYS}</kbd>
        </button>
      </div>
    </header>
  );
}
