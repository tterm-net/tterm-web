// tTerm for Mac updating itself (0.1.15, for the app 0.5.0), as on the
// canvas of 29.10 (version 85): only in the app, signed in or not; tTerm
// Web itself never shows any of it. A version ready says so in two places
// that go together (the owner, 29.10, 17:07: «и А и Б, они дополняют друг
// друга»): Restart to update in the top bar, next to Search, there also
// with the sidebar closed (A), and a card above the account in the sidebar
// (B). After the restart, once: tTerm is updated to 0.5.1, with What's new.
// In Settings, Updates: Update by itself, recommended and the default, or
// Ask me first (the owner, 17:07: «по умолчанию оставить в Обновлять самому
// (написать, что это рекомендованный вариант), но оставить вариант и
// обновлять по разрешению»); how it is now, with Check now; and why not,
// where the app cannot update itself, with Move to Applications from the
// disk image. Restart asks first in the app itself, with the question of
// Quit, when terminals of this Mac would end.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  checkUpdate, loadUpdate, moveToApplications, restartToUpdate, setUpdateMode, updateSeen,
  type AppUpdate, type UpdateMode,
} from "./api";
import { Check, Close, Restart, Warning } from "./icons";
import { hereWord } from "./local";
import { ago, clock, onDay } from "./time";

/** How often the page asks the app, by itself; in Settings, more often. */
const LOOK_EVERY_MS = 60_000;
const LOOK_IN_SETTINGS_MS = 2_000;
/** "tTerm is updated" stays this long once seen, unless closed. */
const UPDATED_MS = 15_000;

interface Updates {
  /** What the app says, or null where it says nothing (the browser). */
  update: AppUpdate | null;
  set: (update: AppUpdate) => void;
  /** Asks the app again, now. */
  look: () => void;
}

export const UpdateState = createContext<Updates>({ update: null, set: () => {}, look: () => {} });

/** The app's updates, asked now and then, and when the window comes back. */
export function useAppUpdate(inApp: boolean): Updates {
  const [update, setUpdate] = useState<AppUpdate | null>(null);
  const [nudge, setNudge] = useState(0);
  useEffect(() => {
    if (!inApp) {
      setUpdate(null);
      return;
    }
    let live = true;
    const look = () => {
      loadUpdate().then((got) => {
        if (live) setUpdate(got);
      }).catch(() => {
        // An app without updates, or none at all: nothing to show.
      });
    };
    look();
    // Asked again while the window is seen; and when it is seen again.
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") look();
    }, LOOK_EVERY_MS);
    const back = () => {
      if (document.visibilityState === "visible") look();
    };
    window.addEventListener("focus", look);
    document.addEventListener("visibilitychange", back);
    return () => {
      live = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", look);
      document.removeEventListener("visibilitychange", back);
    };
  }, [inApp, nudge]);
  const look = useCallback(() => setNudge((n) => n + 1), []);
  return useMemo(() => ({ update, set: setUpdate, look }), [update, look]);
}

/** Restart to update: the app asks first when terminals would end, and
 * otherwise quits for the new version. */
function useRestart() {
  const { set, look } = useContext(UpdateState);
  return useCallback(() => {
    restartToUpdate().then(set).catch(() => look());
  }, [set, look]);
}

function readyVersion(update: AppUpdate | null): string | null {
  return update && (update.state === "ready" || update.state === "installing")
    ? update.next : null;
}

/** A, in the top bar: Restart to update, with what it means on hover. */
export function UpdatePill() {
  const { update } = useContext(UpdateState);
  const restart = useRestart();
  const version = readyVersion(update);
  if (!update || !version) return null;
  const installing = update.state === "installing";
  return (
    <button type="button" className="update-pill" disabled={installing}
      onClick={restart}
      title={`tTerm ${version} is ready. The restart takes a few seconds; if terminals run on ${hereWord()}, tTerm asks first.`}>
      <Restart size={14} />
      <span className="update-pill-text">{installing ? "Restarting…" : "Restart to update"}</span>
    </button>
  );
}

/** B, in the sidebar above the account. */
export function UpdateCard() {
  const { update } = useContext(UpdateState);
  const restart = useRestart();
  const version = readyVersion(update);
  if (!update || !version) return null;
  const installing = update.state === "installing";
  return (
    <div className="update-card" role="status">
      <div className="update-card-head">
        <Restart size={16} />
        <span className="update-card-text">
          <strong>tTerm {version} is ready</strong>
          <span>The restart takes a few seconds.</span>
        </span>
      </div>
      <div className="update-card-actions">
        <button type="button" className="button-accent" disabled={installing}
          onClick={restart}>
          {installing ? "Restarting…" : "Restart"}
        </button>
        {update.notes && <a href={update.notes} target="_blank" rel="noreferrer">What's new</a>}
      </div>
    </div>
  );
}

/** Once, after the restart: tTerm is updated to 0.5.1. Counted from when
 * it is seen: started in the menu bar, the window may open much later. */
export function UpdatedToast() {
  const { update, set } = useContext(UpdateState);
  const updated = update?.updated ?? null;
  const [visible, setVisible] = useState(document.visibilityState === "visible");
  useEffect(() => {
    const on = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  const done = useCallback(() => {
    updateSeen().then(set).catch(() => {});
  }, [set]);
  useEffect(() => {
    if (!updated || !visible) return;
    const timer = window.setTimeout(done, UPDATED_MS);
    return () => window.clearTimeout(timer);
  }, [updated, visible, done]);
  if (!updated || !visible) return null;
  return (
    <div className="update-toast" role="status">
      <span className="update-toast-mark" aria-hidden="true"><Check size={13} /></span>
      <strong>tTerm is updated to {updated.to}</strong>
      <a href={updated.notes} target="_blank" rel="noreferrer">What's new</a>
      <button type="button" className="icon-button tiny" aria-label="Close"
        onClick={done}>
        <Close size={13} />
      </button>
    </div>
  );
}

const MODES: { value: UpdateMode; label: string; recommended?: boolean; text: (mac: string) => string }[] = [
  {
    value: "auto",
    label: "Update by itself",
    recommended: true,
    text: (mac) => `A new version goes in as tTerm quits, and, with tTerm in the menu bar and its window closed, as soon as nothing runs on ${mac}.`,
  },
  {
    value: "ask",
    label: "Ask me first",
    text: () => "A new version waits until you press Restart to update.",
  },
];

/** Settings: Updates. */
export function UpdatesSection() {
  const { update, set } = useContext(UpdateState);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const restart = useRestart();
  const mac = hereWord();

  useEffect(() => {
    let live = true;
    const look = () => {
      if (document.visibilityState !== "visible") return;
      loadUpdate().then((got) => {
        if (live) set(got);
      }).catch(() => {});
    };
    const timer = window.setInterval(look, LOOK_IN_SETTINGS_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [set]);

  if (!update) return null;

  const act = (what: () => Promise<AppUpdate | void>) => async () => {
    setBusy(true);
    setProblem(null);
    try {
      const got = await what();
      if (got) set(got);
    } catch (error) {
      setProblem((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const choose = (mode: UpdateMode) => {
    if (mode === update.mode) return;
    const before = update;
    set({ ...update, mode });
    setBusy(true);
    setProblem(null);
    setUpdateMode(mode).then(set).catch((error: Error) => {
      // Not saved: the choice as it was.
      set(before);
      setProblem(error.message);
    }).finally(() => setBusy(false));
  };

  return (
    <section className="account-section" aria-labelledby="st-updates">
      <div className="account-section-head">
        <h2 id="st-updates">Updates</h2>
        <p>
          tTerm for Mac updates itself from tTerm's releases. Only a version
          signed by tTerm goes in.
        </p>
      </div>
      <div className="account-card update-modes" role="radiogroup" aria-label="How tTerm updates">
        {MODES.map((m) => (
          <label key={m.value} className="update-mode">
            <input type="radio" name="update-mode" value={m.value}
              checked={update.mode === m.value} disabled={busy}
              onChange={() => choose(m.value)} />
            <span className="update-mode-text">
              <span className="update-mode-name">
                {m.label}
                {m.recommended && <span className="update-recommended">Recommended</span>}
              </span>
              <span>{m.text(mac)}</span>
            </span>
          </label>
        ))}
        <UpdateLine update={update} busy={busy} mac={mac}
          onCheck={act(checkUpdate)} onRestart={restart}
          onMove={act(moveToApplications)} />
      </div>
      {problem && <div className="error-box" role="alert">{problem}</div>}
    </section>
  );
}

function UpdateLine(props: {
  update: AppUpdate; busy: boolean; mac: string;
  onCheck: () => void; onRestart: () => void; onMove: () => void;
}) {
  const { update } = props;
  let body;
  switch (update.state) {
    case "checking":
      body = <span className="waiting"><span className="spinner" />Looking for a new version…</span>;
      break;
    case "downloading":
      body = <span className="waiting"><span className="spinner" />Downloading tTerm {update.next}…</span>;
      break;
    case "installing":
      body = <span className="waiting"><span className="spinner" />Restarting…</span>;
      break;
    case "ready":
      body = (
        <>
          <span className="update-line-text">
            <strong>tTerm {update.next} is ready</strong>
            <span className="update-line-detail">
              You have {update.version}.
              {update.readyAt && ` Downloaded ${onDay(update.readyAt)} at ${clock(update.readyAt)}.`}
              {update.notes && <> <a href={update.notes} target="_blank" rel="noreferrer">What's new</a></>}
            </span>
          </span>
          <button type="button" className="button-accent" onClick={props.onRestart}>
            Restart to update
          </button>
        </>
      );
      break;
    case "failed":
      body = (
        <>
          <Warning size={16} className="update-line-warn" />
          <span className="update-line-text">
            {update.next ? (
              <>
                <strong>Could not update to {update.next}</strong>
                <span className="update-line-detail">{update.problem}</span>
              </>
            ) : <strong>{update.problem}</strong>}
            <span className="update-line-detail">
              Or download tTerm from <a href="https://tterm.net" target="_blank" rel="noreferrer">tterm.net</a> and
              drag it to Applications.
            </span>
          </span>
          <button type="button" className="button-plain small" disabled={props.busy}
            onClick={props.onCheck}>
            Try again
          </button>
        </>
      );
      break;
    case "unavailable":
      body = (
        <>
          <span className="update-line-text">
            <span className="update-line-detail">{update.problem}</span>
          </span>
          {update.movable && (
            <button type="button" className="button-accent" disabled={props.busy}
              onClick={props.onMove}>
              Move to Applications
            </button>
          )}
        </>
      );
      break;
    default:
      body = (
        <>
          <span className="update-line-text">
            <span>
              {update.checkedAt ? `tTerm ${update.version} is up to date. Looked ${ago(update.checkedAt)}.`
                : `This is tTerm ${update.version}.`}
            </span>
          </span>
          <button type="button" className="button-plain small" disabled={props.busy}
            onClick={props.onCheck}>
            Check now
          </button>
        </>
      );
  }
  return (
    <div className="update-line" role="status">
      {body}
      {update.note && <span className="update-line-note">{update.note}</span>}
    </div>
  );
}
