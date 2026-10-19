// The account page (0.0.10), from the account at the bottom of the sidebar
// and from the palette, as approved on the canvas on 16.10: the name, which
// the person changes here; the plan and what counts against it; the trusted
// devices; the theme, and from 0.1.1 how much of a project's color the
// sidebar shows; Sign out, which only this page has; and where the
// documents and support are. Tabs stay above it; Close or Esc goes back.

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  loadAccount, saveName, type AccountInfo, type Me, type ProjectTint, type Theme,
} from "./api";
import { DevicesSection } from "./Devices";
import { Close, Monitor, Moon, SignOut, Sun } from "./icons";

interface Props {
  me: Me;
  theme: Theme;
  onTheme: (theme: Theme) => void;
  tint: ProjectTint;
  onTint: (tint: ProjectTint) => void;
  onRenamed: (named: { name: string; firstName: string; initials: string }) => void;
  /** Sign out: this browser forgets the person. */
  onSignOut: () => Promise<void>;
  /** The session ended from here (a device forgotten), and why. */
  onSignedOut: (note: string) => void;
  onClose: () => void;
  notify: (text: string) => void;
}

const THEMES: { value: Theme; label: string; icon: ReactNode }[] = [
  { value: "system", label: "System", icon: <Monitor size={14} /> },
  { value: "light", label: "Light", icon: <Sun size={14} /> },
  { value: "dark", label: "Dark", icon: <Moon size={14} /> },
];

const TINTS: { value: ProjectTint; label: string }[] = [
  { value: "badge", label: "Code badge" },
  { value: "block", label: "Whole project" },
];

const SUPPORT = "support@tterm.net";

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

export function Account(props: Props) {
  const { me } = props;
  const [info, setInfo] = useState<AccountInfo | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(me.name);
  const [nameProblem, setNameProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const close = useRef(props.onClose);
  close.current = props.onClose;
  const editingNow = useRef(false);
  editingNow.current = editing;
  const nameInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadAccount().then(setInfo)
      .catch((error: Error) => setProblem(error.message));
  }, []);

  // Esc goes back, unless a dialog or the palette is open over the page, or
  // the name is being typed (its own Esc puts it back).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (editingNow.current) return;
      if (document.querySelector(".scrim, .palette-backdrop")) return;
      close.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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

  const signOut = () => {
    setLeaving(true);
    props.onSignOut().catch(() => setLeaving(false));
  };

  const support = info?.legal.support ?? SUPPORT;

  return (
    <main className="account-view">
      <div className="account-head">
        <h1>Account</h1>
        <button type="button" className="account-close" onClick={props.onClose}
          title="Back to where you were">
          <Close size={14} />
          Close
          <kbd>Esc</kbd>
        </button>
      </div>

      <section className="account-card profile-card" aria-label="Profile">
        <div className="profile-row">
          <span className="avatar big" aria-hidden="true">{me.initials}</span>
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

      {info && (
        <DevicesSection devices={info.devices} onSignedOut={props.onSignedOut}
          notify={props.notify} />
      )}

      <section className="account-section" aria-labelledby="ac-appearance">
        <div className="account-section-head">
          <h2 id="ac-appearance">Appearance</h2>
          <p>Saved to your account. System follows the setting of each computer
            you use. A project's color fills its code badge, or tints the whole
            project in the sidebar.</p>
        </div>
        <div className="account-card theme-card">
          <span className="theme-label">Theme</span>
          <div className="segmented" role="group" aria-label="Theme">
            {THEMES.map((t) => (
              <button key={t.value} type="button"
                aria-pressed={props.theme === t.value}
                onClick={() => props.onTheme(t.value)}>
                {t.icon}
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <div className="account-card theme-card">
          <span className="theme-label">Project colors</span>
          <div className="segmented" role="group" aria-label="Project colors">
            {TINTS.map((t) => (
              <button key={t.value} type="button"
                aria-pressed={props.tint === t.value}
                onClick={() => props.onTint(t.value)}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="account-card signout-card" aria-labelledby="ac-signout">
        <span className="signout-text">
          <h2 id="ac-signout">Sign out</h2>
          <span>
            This browser forgets you, and next time it asks for a code from
            email. That is also how you switch to another account.
          </span>
        </span>
        <button type="button" className="button-plain" disabled={leaving}
          onClick={signOut}>
          <SignOut size={15} />
          Sign out
        </button>
      </section>

      <footer className="account-foot">
        <span className="account-links">
          {info && (
            <>
              <a href={info.legal.terms} target="_blank" rel="noreferrer">
                Terms of Service
              </a>
              <a href={info.legal.privacy} target="_blank" rel="noreferrer">
                Privacy Policy
              </a>
            </>
          )}
          <a href={`mailto:${support}`}>{support}</a>
        </span>
        <span className="hint">
          To delete your account, write to {support} from the address you sign
          in with.
        </span>
      </footer>
    </main>
  );
}
