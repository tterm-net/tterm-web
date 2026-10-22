// Settings (0.1.4), the other page behind the menu of the account at the
// bottom of the sidebar, as approved on the canvas of 21.10: what was on
// the account page besides the name and the plan. The theme and how much of
// a project's color the sidebar shows; the trusted devices with Forget and
// Forget all devices; the documents and support. In the app before signing
// in (local.ts) there is no account and so no devices, and the choices are
// kept on this computer. Tabs stay above it; Close or Esc goes back.

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  loadAccount, type AccountInfo, type Legal, type ProjectTint, type Theme,
} from "./api";
import { PageHead, SUPPORT, useEscCloses } from "./Account";
import { DevicesSection } from "./Devices";
import { Monitor, Moon, Sun } from "./icons";

interface Props {
  /** The app before signing in: no account, the choices kept here. */
  local: boolean;
  theme: Theme;
  onTheme: (theme: Theme) => void;
  tint: ProjectTint;
  onTint: (tint: ProjectTint) => void;
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

/** Where the links go in the app before signing in: there is no account to
 * ask, and these are the app's own. */
const LEGAL: Legal = {
  terms: "https://tterm.net/terms",
  privacy: "https://tterm.net/privacy",
  support: SUPPORT,
};

export function Settings(props: Props) {
  const { local } = props;
  const [info, setInfo] = useState<AccountInfo | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const nothingTyped = useRef(false);

  useEffect(() => {
    // Devices are the account's: before signing in there are none to load.
    if (local) return;
    loadAccount().then(setInfo)
      .catch((error: Error) => setProblem(error.message));
  }, [local]);

  useEscCloses(props.onClose, nothingTyped);

  // Signed in, the links are the core's, as on the sign-in screen.
  const legal = local ? LEGAL : info?.legal;

  return (
    <main className="account-view" aria-labelledby="st-title">
      <PageHead title="Settings" id="st-title" onClose={props.onClose} />

      <section className="account-section" aria-labelledby="st-appearance">
        <div className="account-section-head">
          <h2 id="st-appearance">Appearance</h2>
          <p>
            {local ? "Saved on this computer. System follows the setting of this computer."
              : "Saved to your account. System follows the setting of each computer you use."}
            {" "}A project's color fills its code badge, or tints the whole
            project in the sidebar.
          </p>
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

      {!local && (info ? (
        <DevicesSection devices={info.devices} onSignedOut={props.onSignedOut}
          notify={props.notify} />
      ) : (
        <section className="account-section" aria-labelledby="st-devices">
          <div className="account-section-head">
            <h2 id="st-devices">Devices</h2>
          </div>
          <div className="account-card devices-card">
            {problem ? <div className="error-box" role="alert">{problem}</div>
              : <div className="waiting"><span className="spinner" />Loading…</div>}
          </div>
        </section>
      ))}

      {legal && (
        <footer className="account-foot">
          <span className="account-links">
            <a href={legal.terms} target="_blank" rel="noreferrer">Terms of Service</a>
            <a href={legal.privacy} target="_blank" rel="noreferrer">Privacy Policy</a>
            <a href={`mailto:${legal.support}`}>{legal.support}</a>
          </span>
        </footer>
      )}
    </main>
  );
}
