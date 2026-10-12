// The first screen: nothing is connected yet, and it should be clear what to
// do. From the approved mockup "Первый вход", without the passkey offer and
// the invitations line: sign-in and teams come in later stages.

import type { MachineKind, Me, Project } from "./api";
import { ArrowRight, Computer, Logo, Server } from "./icons";

interface Props {
  me: Me;
  personal: Project | undefined;
  onAdd: (kind: MachineKind) => void;
}

export function Welcome({ me, personal, onAdd }: Props) {
  return (
    <main className="welcome">
      <div className="welcome-head">
        <Logo size={40} />
        <h1>Welcome to tTerm, {me.firstName}</h1>
        <p className="lead">
          A terminal to your servers and computers, right in the browser.
          Nothing to install on this computer, no keys to copy, no ports to
          open.
        </p>
      </div>

      <section className="connect" aria-labelledby="w-connect">
        <h2 id="w-connect">Connect your first machine</h2>
        <div className="connect-cards">
          <button type="button" className="connect-card"
            onClick={() => onAdd("server")}>
            <span className="card-top">
              <span className="tile" aria-hidden="true"><Server size={20} /></span>
              <span className="arrow"><ArrowRight /></span>
            </span>
            <span className="card-text">
              <span className="card-title">Server</span>
              <span className="card-note">
                A VPS or cloud server you reach over SSH. Run one command on
                it, and it appears here.
              </span>
            </span>
            <span className="chips">
              <span className="chip">Linux</span>
              <span className="chip">One command</span>
            </span>
          </button>
          <button type="button" className="connect-card"
            onClick={() => onAdd("computer")}>
            <span className="card-top">
              <span className="tile" aria-hidden="true">
                <Computer size={20} />
              </span>
              <span className="arrow"><ArrowRight /></span>
            </span>
            <span className="card-text">
              <span className="card-title">Computer</span>
              <span className="card-note">
                A Mac or Linux machine at home or in the office, even behind
                NAT. A small agent keeps it connected.
              </span>
            </span>
            <span className="chips">
              <span className="chip">macOS</span>
              <span className="chip">Linux</span>
              <span className="chip">No open ports</span>
            </span>
          </button>
        </div>
      </section>

      <ol className="steps" aria-label="How it works">
        <li>
          <span className="step-number" aria-hidden="true">1</span>
          <span className="step-text">
            <strong>Add a machine</strong>
            <span>Run one command on it. It works once, for 10 minutes.</span>
          </span>
        </li>
        <li>
          <span className="step-number" aria-hidden="true">2</span>
          <span className="step-text">
            <strong>Open a terminal</strong>
            <span>Each command becomes a block: copy it, run it again, share
              a link.</span>
          </span>
        </li>
        <li>
          <span className="step-number" aria-hidden="true">3</span>
          <span className="step-text">
            <strong>Bring your team</strong>
            <span>Roles, a shared journal, and you always see who is
              watching.</span>
          </span>
        </li>
      </ol>

      {personal && (
        <p className="footnote">
          Your first project, {personal.name}, is ready, with the code{" "}
          {personal.code}: its machines show up in tabs as {personal.code}/name.
        </p>
      )}
    </main>
  );
}
