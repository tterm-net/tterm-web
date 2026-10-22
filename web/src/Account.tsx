// The account page (0.0.10), from 0.1.4 one of two pages behind the menu of
// the account at the bottom of the sidebar, as approved on the canvas of
// 21.10: the name, which the person changes here, the address and how to
// change it; the plan and what counts against it; how to delete the
// account. The theme, project colors, devices and support's address are on
// Settings (Settings.tsx), and Sign out is in the menu itself. Tabs stay
// above it; Close or Esc goes back.

import { useEffect, useRef, useState } from "react";
import { loadAccount, saveName, type AccountInfo, type Me } from "./api";
import { Close } from "./icons";

interface Props {
  me: Me;
  onRenamed: (named: { name: string; firstName: string; initials: string }) => void;
  onClose: () => void;
}

export const SUPPORT = "support@tterm.net";

function limitsText(limits: AccountInfo["plan"]["limits"]): string {
  const parts = [
    limits.machines !== null ? `${limits.machines} machines` : null,
    limits.terminals !== null ? `${limits.terminals} open terminals` : null,
  ].filter(Boolean);
  return parts.length ? `Up to ${parts.join(" and ")}` : "No limits";
}

function Meter(props: { label: string; used: number; most: number | null }) {
  const { label, used, most } = props;
  const share = most ? Math.min(100, (used / most) * 100) : 0;
  return (
    <div className="meter">
      <span className="meter-text">
        <strong>{label}</strong>
        <span>{most !== null ? `${used} of ${most}` : `${used}, no limit`}</span>
      </span>
      {most !== null && (
        <span className="meter-bar" aria-hidden="true">
          <span className={used > 0 ? "meter-fill some" : "meter-fill"}
            style={{ width: `${share}%` }} />
        </span>
      )}
    </div>
  );
}

/** Esc goes back, unless a dialog or the palette is open over the page, or
 * a field has the keys (its own Esc puts it back). Account and Settings
 * alike. */
export function useEscCloses(onClose: () => void, busy: { current: boolean }) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (busy.current) return;
      if (document.querySelector(".scrim, .palette-backdrop")) return;
      close.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy]);
}

/** The title of Account and Settings, with Close and its Esc. */
export function PageHead(props: { title: string; id: string; onClose: () => void }) {
  return (
    <div className="account-head">
      <h1 id={props.id}>{props.title}</h1>
      <button type="button" className="account-close" onClick={props.onClose}
        title="Back to where you were">
        <Close size={14} />
        Close
        <kbd>Esc</kbd>
      </button>
    </div>
  );
}

export function Account(props: Props) {
  const { me } = props;
  const [info, setInfo] = useState<AccountInfo | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(me.name);
  const [nameProblem, setNameProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const editingNow = useRef(false);
  editingNow.current = editing;
  const nameInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadAccount().then(setInfo)
      .catch((error: Error) => setProblem(error.message));
  }, []);

  useEscCloses(props.onClose, editingNow);

  useEffect(() => {
    if (editing) nameInput.current?.select();
  }, [editing]);

  const startEdit = () => {
    setDraft(me.name);
    setNameProblem(null);
    setEditing(true);
  };
  const cancelEdit = () => {
    setEditing(false);
    setNameProblem(null);
  };
  const save = async () => {
    if (saving) return;
    if (draft.trim() === me.name) {
      cancelEdit();
      return;
    }
    setSaving(true);
    setNameProblem(null);
    try {
      props.onRenamed(await saveName(draft));
      setEditing(false);
    } catch (error) {
      setNameProblem((error as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const support = info?.legal.support ?? SUPPORT;

  return (
    <main className="account-view" aria-labelledby="ac-title">
      <PageHead title="Account" id="ac-title" onClose={props.onClose} />

      <section className="account-card profile-card" aria-label="Profile">
        <div className="profile-row">
          <span className="avatar big mine" aria-hidden="true">{me.initials}</span>
          {editing ? (
            <form className="profile-edit" onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}>
              <label className="profile-label">
                <span>Your name</span>
                <input ref={nameInput} className="text-input" name="name"
                  autoComplete="name" maxLength={100} value={draft}
                  disabled={saving}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      event.preventDefault();
                      cancelEdit();
                    }
                  }} />
              </label>
              <span className="button-row profile-buttons">
                <button type="submit" className="button-accent"
                  disabled={saving || !draft.trim()}>
                  {saving ? "Saving…" : "Save"}
                </button>
                <button type="button" className="button-plain quiet"
                  disabled={saving} onClick={cancelEdit}>
                  Cancel
                </button>
              </span>
            </form>
          ) : (
            <>
              <span className="profile-text">
                <strong>{me.name}</strong>
                {me.email && <span>{me.email}</span>}
              </span>
              <button type="button" className="button-plain small"
                onClick={startEdit}>
                Edit name
              </button>
            </>
          )}
        </div>
        {nameProblem && <div className="error-box" role="alert">{nameProblem}</div>}
        <p className="profile-note">
          You sign in with this address. To change it, write
          to <a href={`mailto:${support}`}>{support}</a>.
        </p>
      </section>

      <section className="account-section" aria-labelledby="ac-plan">
        <div className="account-section-head">
          <h2 id="ac-plan">Plan</h2>
          <p>
            tTerm is free while we build it. Other plans will appear here, and
            you will hear about them before anything changes.
          </p>
        </div>
        <div className="account-card plan-card">
          {info ? (
            <>
              <div className="plan-line">
                <span className="plan-badge">{info.plan.name}</span>
                <span>{limitsText(info.plan.limits)}</span>
              </div>
              <div className="meters">
                <Meter label="Machines" used={info.usage.machines}
                  most={info.plan.limits.machines} />
                <Meter label="Open terminals" used={info.usage.terminals}
                  most={info.plan.limits.terminals} />
              </div>
            </>
          ) : problem ? (
            <div className="error-box" role="alert">{problem}</div>
          ) : (
            <div className="waiting"><span className="spinner" />Loading…</div>
          )}
        </div>
      </section>

      <footer className="account-foot">
        <span className="hint">
          To delete your account, write to {support} from the address you sign
          in with.
        </span>
      </footer>
    </main>
  );
}
