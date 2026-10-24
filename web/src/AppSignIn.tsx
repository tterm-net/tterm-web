// In tTerm for the computer (0.1.6): signing in with the browser, as on the
// approved canvas (version 63). The welcome on the app's first start, with
// Sign in with your browser and Later; and the window that waits while the
// person signs in in the browser and allows this computer there, with the
// code the browser shows too. The app's engine does the signing in; this
// asks it how it goes, and when it is done the page starts over signed in.

import { useEffect, useRef, useState } from "react";
import { appLinkState, cancelAppLink, signInApp } from "./api";
import { Logo } from "./icons";

const SUPPORT = "support@tterm.net";
/** How often the window asks the engine how signing in goes. */
const ASK_MS = 1000;

export function AppWelcome(props: { onSignIn: () => void; onLater: () => void }) {
  return (
    <main className="signin app-welcome" aria-label="Welcome">
      <header className="signin-brand">
        <Logo size={36} />
        <span aria-hidden="true">tTerm</span>
      </header>
      <div className="signin-middle">
        <section className="signin-card">
          <div className="signin-form">
            <h1>Welcome to tTerm</h1>
            <p className="signin-lead">
              The terminals of this computer work right away, no account
              needed.
            </p>
            <button type="button" className="button-accent big wide app-welcome-in"
              onClick={props.onSignIn}>
              Sign in with your browser
            </button>
            <button type="button" className="button-plain big wide app-welcome-later"
              onClick={props.onLater}>
              Later
            </button>
            <p className="app-welcome-note">
              Signing in brings your servers, projects, notes and team into
              this window. You can do it any time, from the bottom of the
              sidebar.
            </p>
          </div>
        </section>
      </div>
      <footer className="signin-footer">
        <a href={`mailto:${SUPPORT}`}>{SUPPORT}</a>
      </footer>
    </main>
  );
}

interface LinkProps {
  /** The code the browser shows too. */
  code: string;
  /** Over: signed in (the page starts over), or not, and why. */
  onDone: () => void;
  onOver: (why: string | null) => void;
}

export function AppLinkWindow(props: LinkProps) {
  const box = useRef<HTMLElement>(null);
  const over = useRef(props.onOver);
  over.current = props.onOver;
  const done = useRef(props.onDone);
  done.current = props.onDone;
  const [problem, setProblem] = useState<string | null>(null);

  const cancel = () => {
    cancelAppLink().catch(() => {});
    over.current(null);
  };
  const cancelRef = useRef(cancel);
  cancelRef.current = cancel;

  useEffect(() => {
    box.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) {
        event.preventDefault();
        cancelRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    let stopped = false;
    const ask = () => {
      appLinkState()
        .then((now) => {
          if (stopped) return;
          if (now.state === "done") {
            stopped = true;
            done.current();
          } else if (now.state === "waiting") {
            timer = window.setTimeout(ask, ASK_MS);
          } else {
            stopped = true;
            over.current(now.state === "cancelled"
              ? "Signing in was cancelled in the browser."
              : now.error ?? null);
          }
        })
        .catch(() => {
          if (!stopped) timer = window.setTimeout(ask, ASK_MS);
        });
    };
    let timer = window.setTimeout(ask, ASK_MS);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  const again = () => {
    setProblem(null);
    signInApp(true).catch((error: Error) => setProblem(error.message));
  };

  return (
    <div className="scrim">
      <section ref={box} className="dialog app-link" role="dialog"
        aria-modal="true" aria-labelledby="al-title" aria-describedby="al-note"
        tabIndex={-1}>
        <h2 id="al-title">Sign in with your browser</h2>
        <p id="al-note">
          tTerm opened app.tterm.net in your browser. Sign in there, and allow
          this computer when the page asks. The page shows this code too:
        </p>
        <span className="app-link-code">{props.code}</span>
        <span className="app-link-small">
          Until then, this computer works as before.
        </span>
        {problem && <div className="error-box" role="alert">{problem}</div>}
        <div className="app-link-buttons">
          <button type="button" className="button-plain" onClick={again}>
            Open the page again
          </button>
          <button type="button" className="button-plain app-link-cancel"
            onClick={cancel}>
            Cancel
          </button>
        </div>
      </section>
    </div>
  );
}
