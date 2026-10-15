// Asking before what cannot be undone: removing a machine, ending a
// terminal that runs. Cancel has the keys, so that Enter by habit does
// nothing; the red button does it.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Close } from "./icons";

interface Props {
  title: string;
  children: ReactNode;
  /** The red button's words: "Remove MacBook-Pro". */
  confirm: string;
  onConfirm: () => Promise<void> | void;
  onCancel: () => void;
}

export function Confirm(props: Props) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const box = useRef<HTMLElement>(null);
  const cancel = useRef(props.onCancel);
  cancel.current = props.onCancel;

  useEffect(() => {
    box.current?.querySelector<HTMLButtonElement>(".confirm-cancel")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") cancel.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const go = async () => {
    setBusy(true);
    setProblem(null);
    try {
      await props.onConfirm();
    } catch (e) {
      setProblem((e as Error).message || "tTerm Web did not answer.");
      setBusy(false);
    }
  };

  return (
    <div className="scrim">
      <section ref={box} className="dialog confirm-dialog" role="alertdialog"
        aria-modal="true" aria-labelledby="cf-title" tabIndex={-1}>
        <div className="dialog-head">
          <div className="dialog-title">
            <h2 id="cf-title">{props.title}</h2>
          </div>
          <button type="button" className="icon-button small" aria-label="Close"
            onClick={props.onCancel}>
            <Close size={16} />
          </button>
        </div>
        <div className="confirm-body">{props.children}</div>
        {problem && <div className="error-box" role="alert">{problem}</div>}
        <div className="button-row">
          <button type="button" className="button-danger big" disabled={busy}
            onClick={() => void go()}>
            {props.confirm}
          </button>
          <button type="button" className="button-plain confirm-cancel"
            onClick={props.onCancel}>
            Cancel
          </button>
        </div>
      </section>
    </div>
  );
}

/** After a server is removed: the command that takes tTerm Web's access back
 * on the server itself, to copy. */
export function Removed(props: {
  name: string;
  cleanup: string;
  onClose: () => void;
  notify: (text: string) => void;
}) {
  const box = useRef<HTMLElement>(null);
  const close = useRef(props.onClose);
  close.current = props.onClose;
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    box.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(props.cleanup);
      setCopied(true);
    } catch {
      props.notify("Could not copy: select the command and copy it by hand.");
    }
  };

  return (
    <div className="scrim">
      <section ref={box} className="dialog confirm-dialog" role="dialog"
        aria-modal="true" aria-labelledby="rm-title" tabIndex={-1}>
        <div className="dialog-head">
          <div className="dialog-title">
            <h2 id="rm-title">{props.name} is removed</h2>
          </div>
          <button type="button" className="icon-button small" aria-label="Close"
            onClick={props.onClose}>
            <Close size={16} />
          </button>
        </div>
        <div className="confirm-body">
          <p>
            tTerm Web no longer lists it or signs in to it. The server still
            trusts tTerm Web's certificates, until you take that back there:
          </p>
          <div className="command-box">
            <code>{props.cleanup}</code>
            <button type="button" className="button-accent" onClick={copy}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <p className="hint">
            The user tterm and its sudo rule stay, for the tTerm bot if you use
            it. To remove them too: sudo userdel -r tterm && sudo rm -f
            /etc/sudoers.d/tterm
          </p>
        </div>
        <div className="button-row">
          <button type="button" className="button-plain" onClick={props.onClose}>
            Done
          </button>
        </div>
      </section>
    </div>
  );
}
