// New project and Add machine in the app before signing in (0.1.5, as on
// the canvas of 22.10, version 65): they are there as after signing in, so
// that the person sees what tTerm has, and open this instead. What comes
// with an account, drawn dashed as not here yet; what the Free plan gives;
// Sign in, the same as in the account's menu.

import { useEffect, useRef, type ReactNode } from "react";
import { Close, Computer, People, Person, Server } from "./icons";

/** What was asked for: a machine, or a project. */
export type Need = "machine" | "project";

/** The Free plan, which a new account gets, as the core has it: a change
 * there wants these words changed too. */
const FREE = { machines: 50, terminals: 24 };

interface Card {
  icon: ReactNode;
  title: string;
  text: string;
  /** A word on what is not there yet. */
  pill?: string;
}

const WHAT: Record<Need, { title: string; note: string; cards: Card[]; plan: string; stays: string }> = {
  machine: {
    title: "Add a machine",
    note: "Servers and other computers come with a tTerm account. Signing in "
      + "makes one, if you have none yet.",
    cards: [
      { icon: <Server size={16} />, title: "Server",
        text: "A VPS or cloud server you reach over SSH. Run one command on it, "
          + "and it appears here." },
      { icon: <Computer size={16} />, title: "Computer",
        text: "A Mac or Linux machine at home or in the office, even behind NAT." },
    ],
    plan: `Up to ${FREE.machines} machines and ${FREE.terminals} open terminals.`,
    stays: "This computer stays as it is, with its terminals.",
  },
  project: {
    title: "New project",
    note: "More projects come with a tTerm account. Signing in makes one, if "
      + "you have none yet.",
    cards: [
      { icon: <Person size={16} />, title: "Personal projects",
        text: "Only you. Its machines, terminals and notes are yours alone." },
      { icon: <People size={16} />, title: "Shared projects", pill: "Soon",
        text: "With your team. You invite people: they open terminals on its "
          + "machines and edit its notes." },
    ],
    plan: "Projects of your own, each with its machines, terminals and notes. "
      + "Shared projects with a team come soon.",
    stays: "Personal stays as it is, with this computer, its terminals and "
      + "notes.",
  },
};

export function NeedAccount(props: {
  need: Need;
  onSignIn: () => void;
  onClose: () => void;
}) {
  const box = useRef<HTMLElement>(null);
  const close = useRef(props.onClose);
  close.current = props.onClose;
  const what = WHAT[props.need];

  useEffect(() => {
    // The window itself has the keys: Enter by habit signs nobody in.
    box.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) {
        event.preventDefault();
        close.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="scrim">
      <section ref={box} className="dialog need-dialog" role="dialog"
        aria-modal="true" aria-labelledby="na-title" aria-describedby="na-note"
        tabIndex={-1}>
        <div className="dialog-head">
          <div className="dialog-title">
            <h2 id="na-title">{what.title}</h2>
            <p className="dialog-note" id="na-note">{what.note}</p>
          </div>
          <button type="button" className="icon-button small" aria-label="Close"
            onClick={props.onClose}>
            <Close size={16} />
          </button>
        </div>
        <ul className="need-cards">
          {what.cards.map((card) => (
            <li key={card.title} className="need-card">
              <span className="need-card-head">
                <span className="need-icon" aria-hidden="true">{card.icon}</span>
                <strong>{card.title}</strong>
                {card.pill && <span className="need-pill">{card.pill}</span>}
              </span>
              <span>{card.text}</span>
            </li>
          ))}
        </ul>
        <div className="need-plan">
          <span className="plan-badge">Free</span>
          <span className="need-plan-text">
            <strong>{what.plan}</strong>
            <span>{what.stays}</span>
          </span>
        </div>
        <div className="button-row need-buttons">
          <button type="button" className="button-plain" onClick={props.onClose}>
            Not now
          </button>
          <button type="button" className="button-accent" onClick={props.onSignIn}>
            Sign in
          </button>
        </div>
      </section>
    </div>
  );
}
