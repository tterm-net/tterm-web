// The browsers where the person signs in with a quick code, from the menu
// of their account. Forget one, and there it is email again; Sign out
// everywhere ends every session and forgets them all.

import { useEffect, useRef, useState } from "react";
import { forgetDevice, listDevices, signOutEverywhere, type Device } from "./api";
import { Close } from "./icons";
import { since } from "./time";

interface Props {
  onClose: () => void;
  /** This browser is signed out now, and why. */
  onSignedOut: (note: string) => void;
  notify: (text: string) => void;
}

function when(ms: number): string {
  const ago = since(ms);
  return ago === "less than a minute" ? "just now" : `${ago} ago`;
}

export function Devices(props: Props) {
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLElement>(null);
  const close = useRef(props.onClose);
  close.current = props.onClose;

  useEffect(() => {
    box.current?.focus();
    listDevices().then(setDevices)
      .catch((error: Error) => setProblem(error.message));
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const forget = async (device: Device) => {
    setProblem(null);
    try {
      const result = await forgetDevice(device.id);
      if (result.current) {
        props.onSignedOut("This device is forgotten. Sign in with a code " +
          "from email.");
        return;
      }
      setDevices((all) => all?.filter((d) => d.id !== device.id) ?? null);
      props.notify(`${device.label} is forgotten: there it is email again.`);
    } catch (error) {
      setProblem((error as Error).message);
    }
  };

  const everywhere = async () => {
    setBusy(true);
    setProblem(null);
    try {
      await signOutEverywhere();
      props.onSignedOut("You signed out everywhere. Each device signs in " +
        "with a code from email now.");
    } catch (error) {
      setProblem((error as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="scrim">
      <section ref={box} className="dialog devices-dialog" role="dialog"
        aria-modal="true" aria-labelledby="dv-title" tabIndex={-1}>
        <div className="dialog-head">
          <div className="dialog-title">
            <h2 id="dv-title">Devices</h2>
            <p className="dialog-sub">
              Browsers where you sign in with your quick code. Anywhere else,
              it takes a code from email.
            </p>
          </div>
          <button type="button" className="icon-button small" aria-label="Close"
            onClick={props.onClose}>
            <Close size={16} />
          </button>
        </div>

        {devices === null && !problem && (
          <div className="waiting"><span className="spinner" />Loading…</div>
        )}
        {devices !== null && devices.length === 0 && (
          <p className="note-box">
            No trusted devices. Right after signing in with a code from email,
            set a quick code, and that browser becomes one.
          </p>
        )}
        {devices !== null && devices.length > 0 && (
          <ul className="device-list">
            {devices.map((device) => (
              <li key={device.id} className="device-row">
                <span className="device-text">
                  <strong>
                    {device.label}
                    {device.current && <span className="chip">This device</span>}
                  </strong>
                  <span>
                    Trusted {when(device.createdAt)}, last sign-in{" "}
                    {when(device.lastUsed)}
                  </span>
                </span>
                <button type="button" className="button-plain small"
                  onClick={() => void forget(device)}>
                  Forget
                </button>
              </li>
            ))}
          </ul>
        )}
        {problem && <div className="error-box" role="alert">{problem}</div>}

        <div className="devices-everywhere">
          <h3>Sign out everywhere</h3>
          <p className="hint">
            Every session ends, here and on your other devices, and every
            device is forgotten. Your terminals keep running.
          </p>
          {asking ? (
            <div className="button-row">
              <button type="button" className="button-danger big"
                disabled={busy} onClick={() => void everywhere()}>
                Sign out everywhere
              </button>
              <button type="button" className="button-plain"
                onClick={() => setAsking(false)}>
                Cancel
              </button>
            </div>
          ) : (
            <button type="button" className="button-plain"
              onClick={() => setAsking(true)}>
              Sign out everywhere…
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
