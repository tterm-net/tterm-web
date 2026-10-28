// This computer, in Settings (0.1.14, for the app 0.4.0), as on the canvas
// of 23.10 (version 67, variant B: AppCloud's Settings): only in the app,
// signed in, between Appearance and Devices. Its switch, Open from tTerm Web
// and your phone: on, tTerm Web and the phone open terminals on this
// computer through the app, which stays in the menu bar with its window
// closed and starts when the person logs in (the owner, 28.10, «вариант
// 2»); off, there this computer is offline and here nothing changes. Under
// it, how the link is, asked every two seconds while Settings is open.
// Commands in your history everywhere, the canvas's second switch, comes
// with its own step.

import { useEffect, useState } from "react";
import { loadThisComputer, openFromWeb, type ThisComputer } from "./api";
import { Confirm } from "./Confirm";
import { Computer } from "./icons";
import { hereWord } from "./local";

interface Props {
  /** This computer's machine in tTerm, and its project. */
  here: { name: string; project: string };
}

/** "1 terminal", "2 terminals". */
function terminals(n: number): string {
  return n === 1 ? "1 terminal" : `${n} terminals`;
}

export function ThisComputerSection(props: Props) {
  const [info, setInfo] = useState<ThisComputer | null>(null);
  /** Why the switch did not move: until it is moved again. */
  const [problem, setProblem] = useState<string | null>(null);
  /** Why the app did not answer: until it answers again. */
  const [unheard, setUnheard] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** Off asked for while terminals of tTerm Web run here: asked first. */
  const [asking, setAsking] = useState<number | null>(null);
  const mac = hereWord();
  const Mac = mac.charAt(0).toUpperCase() + mac.slice(1);

  useEffect(() => {
    let live = true;
    const look = () => {
      loadThisComputer()
        .then((got) => {
          if (!live) return;
          setInfo(got);
          setUnheard(null);
        })
        .catch((error: Error) => {
          if (live) setUnheard(error.message);
        });
    };
    look();
    const timer = window.setInterval(look, 2000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, []);

  const set = async (open: boolean) => {
    setBusy(true);
    setProblem(null);
    try {
      setInfo(await openFromWeb(open));
    } catch (error) {
      setProblem((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggle = () => {
    if (!info) return;
    if (info.open && info.terminals > 0) {
      setAsking(info.terminals);
      return;
    }
    void set(!info.open);
  };

  const on = info?.open ?? false;
  let line: { text: string; dot?: "on" | "off"; detail?: string | null } | null = null;
  switch (info?.status) {
    case "online":
      line = {
        dot: "on",
        text: `Online in tTerm Web as ${info.name ?? props.here.name}` +
          (info.terminals > 0 ? ` · ${terminals(info.terminals)} opened from there` : ""),
      };
      break;
    case "connecting":
      line = { text: "Connecting to tTerm Web…", detail: info.problem };
      break;
    case "waiting":
      line = { dot: "off", text: "Not connected to tTerm Web yet.", detail: info.problem };
      break;
    case "no-network":
      line = { dot: "off", text: "No network. tTerm tries again by itself." };
      break;
    case "removed":
      line = {
        dot: "off",
        text: `${Mac} was removed from tTerm Web, so it is not opened from there. Turn this on to add it again.`,
      };
      break;
    case "closed":
      // Off by itself: the agent's file gone, another tTerm Web's, another
      // account's computer.
      line = {
        dot: "off",
        text: `${Mac} is not opened from tTerm Web now.`,
        detail: info.problem,
      };
      break;
  }

  return (
    <section className="account-section" aria-labelledby="st-here">
      <div className="account-section-head">
        <h2 id="st-here" className="here-head">
          <span className="here-mark big" aria-hidden="true"><Computer size={15} /></span>
          This computer
        </h2>
        <p>
          {props.here.name}, in {props.here.project}. Its terminals run here,
          also without the internet.
        </p>
      </div>
      <div className="account-card switch-card">
        <div className="switch-row">
          <span className="switch-text">
            <label htmlFor="st-open">Open from tTerm Web and your phone</label>
            <span>
              tTerm Web and your phone open terminals on {mac} while tTerm
              runs here: with its window closed it stays in the menu bar, and
              it starts when you log in. Off: there {mac} is offline; here
              everything works as before.
            </span>
          </span>
          <button id="st-open" type="button" role="switch" aria-checked={on}
            className="switch" disabled={!info || busy} onClick={toggle}>
            <span aria-hidden="true" />
          </button>
        </div>
        {line && (
          <div className="switch-state" role="status">
            {line.dot && <span className={`state-dot ${line.dot}`} aria-hidden="true" />}
            <span className="switch-state-text">
              {line.text}
              {line.detail && <span className="switch-state-detail">{line.detail}</span>}
            </span>
          </div>
        )}
      </div>
      {(problem ?? unheard) && <div className="error-box" role="alert">{problem ?? unheard}</div>}
      {asking !== null && (
        <Confirm title={`Close ${mac} to tTerm Web?`}
          confirm="Turn off"
          onConfirm={async () => {
            setAsking(null);
            await set(false);
          }}
          onCancel={() => setAsking(null)}>
          <p>
            {terminals(asking)} opened from tTerm Web on {mac} will end, and
            there {mac} goes offline.
          </p>
        </Confirm>
      )}
    </section>
  );
}
