// /connect (0.1.6): tTerm for the computer opened this page in the browser
// to sign in. Signed in here, the person allows it, as on the approved
// canvas (version 63, "Allow tTerm for Mac?"): the code the app shows, Allow
// and Cancel. Allow takes the one-time code to the app on 127.0.0.1, on
// this very computer; "You are in" then comes from the app itself, which
// shows its own page there. Not signed in, the usual sign-in comes first,
// and then this. A session older than a quarter of an hour confirms first,
// with the quick code or a code from email, as for setting a quick code:
// an app gets a key that lasts. Then the page allows by itself.
//
// Signed in as someone else than the app is for (0.1.8; the owner, 25.10:
// "а как мне войти под другим аккаунтом?"): Not you? Use another account
// ends this browser's session and asks for an email; once signed in, the
// same question, for that account.

import { useEffect, useState, type ReactNode } from "react";
import { AccountChanged, allowApp, ConfirmFirst, endSession, loadState, SignedOut, type Me } from "./api";
import { appTitle, cancelAddress, linkCode, readLink } from "./appLink";
import { Computer, Logo } from "./icons";
import { SignIn } from "./SignIn";
import { applyTheme } from "./theme";

type View =
  | { kind: "loading" }
  | { kind: "signin" }
  | { kind: "ask"; me: Me }
  | { kind: "going"; me: Me }
  | { kind: "problem"; text: string };

const SUPPORT = "support@tterm.net";

export function Connect() {
  const [link] = useState(() => readLink(location.search));
  const [view, setView] = useState<View>({ kind: "loading" });
  const [code, setCode] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** Allow was pressed with a session too old for it, by this person (their
   * id): signed in again as them, the page allows by itself; as someone
   * else (Switch account), it asks that person first (0.1.8). */
  const [confirming, setConfirming] = useState<string | null>(null);
  /** Not you?: another account signs in, with its email. */
  const [another, setAnother] = useState(false);

  const allowNow = async (me: Me) => {
    if (!link) return;
    setBusy(true);
    setProblem(null);
    try {
      const { redirect } = await allowApp(link, me.id);
      setView({ kind: "going", me });
      location.assign(redirect);
    } catch (error) {
      setBusy(false);
      if (error instanceof ConfirmFirst) {
        // This session only ends: a trusted browser asks its quick code.
        await endSession().catch(() => {});
        setConfirming(me.id);
        setView({ kind: "signin" });
      } else if (error instanceof SignedOut) {
        setView({ kind: "signin" });
      } else if (error instanceof AccountChanged) {
        // The question again, for whoever is signed in now; the words stay.
        setProblem(error.message);
        load(null, true);
      } else {
        setProblem((error as Error).message);
      }
    }
  };

  /** The question, for whoever is signed in; with `thenAllowFor`, allowed
   * at once if that is still the person who pressed Allow. `keep`: the
   * problem shown stays. */
  const load = (thenAllowFor: string | null = null, keep = false) => {
    if (!keep) setProblem(null);
    loadState(true)
      .then((state) => {
        applyTheme(state.me.theme);
        setView({ kind: "ask", me: state.me });
        if (thenAllowFor !== null && state.me.id === thenAllowFor) void allowNow(state.me);
      })
      .catch((error) => {
        if (error instanceof SignedOut) setView({ kind: "signin" });
        else setView({ kind: "problem", text: "tTerm is not answering. Check " +
          "the connection, then reload this page." });
      });
  };

  useEffect(() => {
    if (!link) return;
    document.title = `Allow ${appTitle(link)}? · tTerm`;
    linkCode(link.challenge).then(setCode).catch(() => setCode(""));
    load();
  }, [link]);

  if (!link) {
    return (
      <Frame>
        <div className="signin-form">
          <h1>This link is not complete</h1>
          <p className="signin-lead">
            Start again in tTerm on your computer: Sign in with your browser.
          </p>
        </div>
      </Frame>
    );
  }

  if (view.kind === "signin") {
    return (
      <SignIn note={confirming !== null ? `To allow ${appTitle(link)}, confirm it is you.`
        : another ? `Sign in with the other account, then allow ${appTitle(link)}.`
        : `Sign in, then allow ${appTitle(link)}.`}
        another={another}
        onSignedIn={() => {
          setConfirming(null);
          setAnother(false);
          load(confirming);
        }} />
    );
  }

  if (view.kind === "loading") {
    return <Frame busy />;
  }

  if (view.kind === "problem") {
    return (
      <Frame>
        <div className="signin-form">
          <h1>No connection</h1>
          <p className="signin-lead">{view.text}</p>
        </div>
      </Frame>
    );
  }

  const allow = () => {
    if (view.kind === "ask") void allowNow(view.me);
  };

  /** This browser's session ends; the account itself, and this browser's
   * quick code, stay. */
  const useAnother = async () => {
    setBusy(true);
    setProblem(null);
    try {
      await endSession();
    } catch (error) {
      // Ended already: the same.
      if (!(error instanceof SignedOut)) {
        setBusy(false);
        setProblem((error as Error).message);
        return;
      }
    }
    setBusy(false);
    setAnother(true);
    setView({ kind: "signin" });
  };

  const title = appTitle(link);
  return (
    <Frame>
      {view.kind === "ask" ? (
        <div className="signin-form connect-form">
          <span className="connect-icon" aria-hidden="true"><Computer size={22} /></span>
          <h1>Allow {title}?</h1>
          <p className="signin-lead connect-lead">
            The app on <strong className="ink">{link.name}</strong> asks to
            sign in to your account, {view.me.email}.
          </p>
          <span className="connect-check" id="connect-check">
            Check that the app shows the same code
          </span>
          <span className="connect-code" aria-describedby="connect-check">
            {code}
          </span>
          <button type="button" className="button-accent big wide connect-allow"
            disabled={busy} onClick={allow}>
            Allow
          </button>
          <button type="button" className="connect-cancel" disabled={busy}
            onClick={() => location.assign(cancelAddress(link))}>
            Cancel
          </button>
          <button type="button" className="connect-another" disabled={busy}
            onClick={() => void useAnother()}>
            Not you? Use another account
          </button>
          {problem && <div className="error-box" role="alert">{problem}</div>}
          <p className="connect-small">
            The computer becomes a machine in Personal. The app appears in
            Settings, under Devices, where you can forget it.
          </p>
        </div>
      ) : (
        <div className="signin-form connect-form" role="status">
          <h1>Back to {title}…</h1>
          <p className="signin-lead connect-lead">
            The app takes it from here, on this computer.
          </p>
        </div>
      )}
    </Frame>
  );
}

function Frame(props: { children?: ReactNode; busy?: boolean }) {
  return (
    <main className="signin" aria-busy={props.busy || undefined}>
      <header className="signin-brand">
        <Logo size={36} />
        <span aria-hidden="true">tTerm</span>
      </header>
      <div className="signin-middle">
        {props.children && <section className="signin-card">{props.children}</section>}
      </div>
      <footer className="signin-footer">
        <a href={`mailto:${SUPPORT}`}>{SUPPORT}</a>
      </footer>
    </main>
  );
}
