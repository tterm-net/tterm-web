// The browsers where the person signs in with a quick code, on the account
// page (from 0.0.10; a dialog of its own in 0.0.9). Forget one, and there
// it is email again; Forget all devices ends every session and forgets them
// all, so that one word means the same for one and for all.

import { useState } from "react";
import { forgetAllDevices, forgetDevice, type Device } from "./api";
import { Computer, Phone } from "./icons";
import { since } from "./time";

interface Props {
  devices: Device[];
  /** This browser is signed out now, and why. */
  onSignedOut: (note: string) => void;
  notify: (text: string) => void;
}

function when(ms: number): string {
  const ago = since(ms);
  return ago === "less than a minute" ? "just now" : `${ago} ago`;
}

/** Phones and tablets by their label, as device_label() writes it. */
function handheld(label: string): boolean {
  return /iPhone|iPad|Android/.test(label);
}

export function DevicesSection(props: Props) {
  const [devices, setDevices] = useState<Device[]>(props.devices);
  const [problem, setProblem] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);

  const forget = async (device: Device) => {
    setProblem(null);
    try {
      const result = await forgetDevice(device.id);
      if (result.current) {
        props.onSignedOut("This device is forgotten. Sign in with a code " +
          "from email.");
        return;
      }
      setDevices((all) => all.filter((d) => d.id !== device.id));
      props.notify(`${device.label} is forgotten: there it is email again.`);
    } catch (error) {
      setProblem((error as Error).message);
    }
  };

  const forgetAll = async () => {
    setBusy(true);
    setProblem(null);
    try {
      await forgetAllDevices();
      props.onSignedOut("All devices are forgotten. Each one signs in with " +
        "a code from email now.");
    } catch (error) {
      setProblem((error as Error).message);
      setBusy(false);
    }
  };

  return (
    <section className="account-section" aria-labelledby="ac-devices">
      <div className="account-section-head">
        <h2 id="ac-devices">Devices</h2>
        <p>
          Browsers where you sign in with your quick code. Anywhere else, it
          takes a code from email.
        </p>
        <p>
          After 8 hours without anything done, tTerm asks for the quick code
          again. Your terminals keep running.
        </p>
      </div>
      <div className="account-card devices-card">
        {devices.length === 0 ? (
          <p className="devices-none">
            No browser is remembered. Each sign-in takes a code from email.
          </p>
        ) : (
          <ul className="device-list">
            {devices.map((device) => (
              <li key={device.id} className="device-row">
                <span className="device-icon" aria-hidden="true">
                  {handheld(device.label) ? <Phone size={17} />
                    : <Computer size={17} />}
                </span>
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
        <div className="forget-all">
          <span className="forget-all-text">
            <strong>Forget all devices</strong>
            <span>
              Every device is forgotten and every session ends, this one too.
              Each browser then asks for a code from email. Your terminals keep
              running.
            </span>
          </span>
          {asking ? (
            <span className="button-row">
              <button type="button" className="button-danger big"
                disabled={busy} onClick={() => void forgetAll()}>
                Forget all devices
              </button>
              <button type="button" className="button-plain"
                onClick={() => setAsking(false)}>
                Cancel
              </button>
            </span>
          ) : (
            <button type="button" className="button-plain"
              onClick={() => setAsking(true)}>
              Forget all devices…
            </button>
          )}
        </div>
        {problem && <div className="error-box" role="alert">{problem}</div>}
      </div>
    </section>
  );
}
