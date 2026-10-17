// Signing in (from 0.0.9), for everyone: a code from email, then, on a
// device the person trusts, a four-digit quick code. The rules live in the
// core, see tterm/core/auth.py. Laid out as a bank's sign-in is: the logo
// and the name above a card that asks one thing at a time, digits typed
// into dashes and taken as soon as the last one is in.
//
//   email → code → (a new address: the name and one tick) →
//   a quick code → the same once more, or Not now
//   a trusted device: its quick code; Forgot the code?, Switch account

import {
  useEffect, useRef, useState, type FormEvent, type ReactNode, type Ref,
} from "react";
import {
  askCode, authState, quickSignIn, Refused, setQuickCode, signUp, verifyCode,
  type KnownDevice, type Legal, type SignedInNow,
} from "./api";
import { Logo } from "./icons";

const CODE_DIGITS = 6;
const PIN_DIGITS = 4;
/** Seconds before the same address may get another code. */
const RESEND_S = 60;
/** Where the links go until the core says. */
const LEGAL: Legal = {
  terms: "https://tterm.net/terms",
  privacy: "https://tterm.net/privacy",
  support: "support@tterm.net",
};

type Step =
  | { kind: "loading" }
  | { kind: "email" }
  | { kind: "code"; email: string }
  | { kind: "signup"; email: string }
  | { kind: "offer"; name: string; fresh: boolean }
  | { kind: "pin"; device: KnownDevice };

interface Props {
  /** Why the page asks again, if it does: "Your session ended…". */
  note?: string | null;
  /** The address to start with: whoever was signed in on this page. */
  email?: string;
  onSignedIn: () => void;
}

function digitsOf(value: string, count: number): string {
  return value.replace(/\D/g, "").slice(0, count);
}

/** The cursor of the input under the dashes stays after the last digit: a
 * click on the first dash or an arrow key would otherwise put the next
 * digit in the middle, while the dashes look as if it came last. */
function keepAtEnd(input: HTMLInputElement): void {
  const end = input.value.length;
  if (input.selectionStart !== end || input.selectionEnd !== end) {
    input.setSelectionRange(end, end);
  }
}

function textOf(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "The server is not answering. Check the connection and try again.";
}

/** A quick code anyone would try first, as the core judges it: one digit
 * four times, or the digits in a row (1234, 9876). */
function tooEasy(pin: string): boolean {
  const steps = new Set<number>();
  for (let i = 1; i < pin.length; i++) steps.add(Number(pin[i]) - Number(pin[i - 1]));
  return new Set(pin).size === 1 || (steps.size === 1 && (steps.has(1) || steps.has(-1)));
}

export function SignIn(props: Props) {
  const { onSignedIn } = props;
  const [step, setStep] = useState<Step>({ kind: "loading" });
  const [email, setEmail] = useState(props.email ?? "");
  const [note, setNote] = useState<string | null>(props.note ?? null);
  const [legal, setLegal] = useState<Legal>(LEGAL);

  // Why the session ended may come after this screen is up: Sign out closes
  // the page's socket a moment before its own answer, "You signed out.",
  // arrives. The later word wins.
  useEffect(() => {
    if (props.note) setNote(props.note);
  }, [props.note]);

  useEffect(() => {
    let stopped = false;
    authState()
      .then((state) => {
        if (stopped) return;
        if (state.legal) setLegal(state.legal);
        if (state.signedIn) onSignedIn();
        else if (state.device) {
          setEmail(state.device.email);
          setStep({ kind: "pin", device: state.device });
        } else setStep({ kind: "email" });
      })
      .catch(() => {
        if (stopped) return;
        setNote("The server is not answering. Check the connection and try " +
          "again.");
        setStep({ kind: "email" });
      });
    return () => {
      stopped = true;
    };
  }, [onSignedIn]);

  const toEmail = (why: string | null, address?: string) => {
    setNote(why);
    if (address !== undefined) setEmail(address);
    setStep({ kind: "email" });
  };
  const toCode = (sent: string) => {
    setEmail(sent);
    setNote(null);
    setStep({ kind: "code", email: sent });
  };
  const inNow = (result: SignedInNow) => {
    setNote(null);
    setStep({ kind: "offer", name: result.name, fresh: result.new });
  };

  let body: ReactNode = null;
  switch (step.kind) {
    case "loading":
      break;
    case "email":
      body = <EmailStep email={email} setEmail={setEmail} onSent={toCode} />;
      break;
    case "code":
      body = (
        <CodeStep email={step.email} onBack={() => toEmail(null)} onIn={inNow}
          onSignUp={(address) => {
            setNote(null);
            setStep({ kind: "signup", email: address });
          }} />
      );
      break;
    case "signup":
      body = (
        <SignUpStep email={step.email} legal={legal} onIn={inNow}
          onBack={(why) => toEmail(why)} />
      );
      break;
    case "offer":
      body = <OfferStep name={step.name} fresh={step.fresh} onDone={onSignedIn} />;
      break;
    case "pin":
      body = (
        <PinStep device={step.device} onIn={onSignedIn} onCode={toCode}
          onEmail={(why) => toEmail(why)} onSwitch={() => toEmail(null, "")} />
      );
      break;
  }

  return (
    <main className="signin" aria-busy={step.kind === "loading"}>
      <header className="signin-brand">
        <Logo size={36} />
        <span aria-hidden="true">tTerm</span>
      </header>
      <div className="signin-middle">
        {body && (
          <section className="signin-card">
            {note && <p className="signin-note" role="status">{note}</p>}
            {body}
          </section>
        )}
      </div>
      {/* The Terms and the Policy are in the sign-up tick; here only the
          way to ask for help. */}
      <footer className="signin-footer">
        <a href={`mailto:${legal.support}`}>{legal.support}</a>
      </footer>
    </main>
  );
}

/** Digits typed into dashes: a real input over them, for the keyboard,
 * pasting and the code from the email's autofill. A quick code shows a dot
 * for each digit. */
function Slots(props: {
  count: number;
  value: string;
  secret?: boolean;
  label: string;
  name: string;
  autoComplete: string;
  /** Being checked: what is typed stays, and nothing more goes in. */
  busy?: boolean;
  disabled?: boolean;
  /** One more at each wrong try: the dashes shake. */
  wrong?: number;
  onChange: (value: string) => void;
  ref?: Ref<HTMLInputElement>;
}) {
  const [focused, setFocused] = useState(false);
  const { count, value } = props;
  const here = focused && !props.disabled && !props.busy && value.length < count
    ? value.length : -1;
  const classes = ["slots"];
  if (props.secret) classes.push("secret");
  if (props.busy) classes.push("busy");
  if (props.disabled) classes.push("off");
  return (
    <div className={classes.join(" ")}>
      <div key={props.wrong ?? 0} className={props.wrong ? "slots-row shake" : "slots-row"}
        aria-hidden="true">
        {Array.from({ length: count }, (_, i) => (
          <span key={i} className={"slot" + (i < value.length ? " filled" : "") +
            (i === here ? " here" : "")}>
            {i < value.length && !props.secret ? value[i] : null}
          </span>
        ))}
      </div>
      <input ref={props.ref} className="slots-input" type="text"
        inputMode="numeric" pattern="[0-9]*" name={props.name}
        autoComplete={props.autoComplete} aria-label={props.label} autoFocus
        spellCheck={false} autoCorrect="off" autoCapitalize="off"
        maxLength={count + 8} value={value} readOnly={props.busy}
        disabled={props.disabled}
        onFocus={(e) => {
          setFocused(true);
          keepAtEnd(e.currentTarget);
        }}
        onBlur={() => setFocused(false)}
        onSelect={(e) => keepAtEnd(e.currentTarget)}
        onChange={(e) => props.onChange(digitsOf(e.target.value, count))} />
    </div>
  );
}

function EmailStep(props: {
  email: string;
  setEmail: (email: string) => void;
  onSent: (email: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      const sent = await askCode(props.email);
      props.onSent(sent.email);
    } catch (error) {
      setProblem(textOf(error));
      setBusy(false);
    }
  };

  return (
    <form className="signin-form" onSubmit={(e) => void submit(e)}>
      <h1>Sign in</h1>
      <p className="signin-lead">Enter your email, and we will send you a code</p>
      <input className="text-input signin-input" type="email" name="email"
        autoComplete="email" autoFocus required spellCheck={false}
        aria-label="Email" placeholder="you@example.com" value={props.email}
        onChange={(e) => props.setEmail(e.target.value)} />
      {problem && <p className="signin-error" role="alert">{problem}</p>}
      <button type="submit" className="button-accent big wide"
        disabled={busy || !props.email.trim()}>
        {busy ? "Sending…" : "Get a code"}
      </button>
      <p className="signin-small">New here? You create an account after the code.</p>
    </form>
  );
}

function CodeStep(props: {
  email: string;
  onBack: () => void;
  onIn: (result: SignedInNow) => void;
  onSignUp: (email: string) => void;
}) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  /** No tries left: only a new code helps. */
  const [spent, setSpent] = useState(false);
  const [wrong, setWrong] = useState(0);
  const [sentAt, setSentAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  const input = useRef<HTMLInputElement>(null);
  const checking = useRef(false);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const wait = Math.max(0, RESEND_S - Math.floor((now - sentAt) / 1000));

  const check = async (value: string) => {
    if (checking.current) return;
    checking.current = true;
    setBusy(true);
    setProblem(null);
    setSent(null);
    try {
      const result = await verifyCode(props.email, value);
      if ("signUp" in result) props.onSignUp(result.email);
      else props.onIn(result);
    } catch (error) {
      setCode("");
      setProblem(textOf(error));
      setWrong((n) => n + 1);
      if (error instanceof Refused && error.left === 0) setSpent(true);
      setBusy(false);
      checking.current = false;
      window.setTimeout(() => input.current?.focus(), 0);
    }
  };

  const typed = (value: string) => {
    setCode(value);
    if (value.length === CODE_DIGITS && !spent) void check(value);
  };

  const resend = async () => {
    setProblem(null);
    setSent(null);
    try {
      await askCode(props.email);
      setSentAt(Date.now());
      setNow(Date.now());
      setSpent(false);
      setCode("");
      setSent("A new code is on its way. The one before no longer works.");
      window.setTimeout(() => input.current?.focus(), 0);
    } catch (error) {
      setProblem(textOf(error));
      if (error instanceof Refused && error.wait && error.wait < RESEND_S) {
        setSentAt(Date.now() - (RESEND_S - error.wait) * 1000);
      }
    }
  };

  return (
    <form className="signin-form" onSubmit={(e) => {
      e.preventDefault();
      if (code.length === CODE_DIGITS && !spent) void check(code);
    }}>
      <h1>Enter the code</h1>
      <p className="signin-lead">
        We sent it to <strong className="ink">{props.email}</strong>
      </p>
      <Slots ref={input} count={CODE_DIGITS} value={code} label="Code from the email"
        name="code" autoComplete="one-time-code" busy={busy} disabled={spent}
        wrong={wrong} onChange={typed} />
      {problem && <p className="signin-error" role="alert">{problem}</p>}
      {sent && <p className="signin-ok" role="status">{sent}</p>}
      <div className="signin-links">
        <button type="button" className="link-button" disabled={wait > 0}
          onClick={() => void resend()}>
          {wait > 0 ? `New code in ${wait} s` : "Send a new code"}
        </button>
        <button type="button" className="link-button" onClick={props.onBack}>
          Change email
        </button>
      </div>
      {wait === 0 && !sent && (
        <p className="signin-small">No email? Look in the spam folder.</p>
      )}
    </form>
  );
}

function SignUpStep(props: {
  email: string;
  legal: Legal;
  onIn: (result: SignedInNow) => void;
  onBack: (why: string | null) => void;
}) {
  const [name, setName] = useState("");
  /** The one tick: the Terms of Service and the Privacy Policy. */
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      props.onIn(await signUp(props.email, name, agree));
    } catch (error) {
      if (error instanceof Refused && error.status === 410) {
        props.onBack(error.message);
        return;
      }
      setProblem(textOf(error));
      setBusy(false);
    }
  };

  return (
    <form className="signin-form" onSubmit={(e) => void submit(e)}>
      <h1>Create your account</h1>
      <p className="signin-lead">{props.email}</p>
      <input className="text-input signin-input" name="name" autoComplete="name"
        autoFocus required maxLength={100} aria-label="Your name"
        placeholder="Your name" value={name}
        onChange={(e) => setName(e.target.value)} />
      <label className="signin-check">
        <input type="checkbox" name="agree" checked={agree}
          onChange={(e) => setAgree(e.target.checked)} />
        <span>
          I agree to the{" "}
          <a href={props.legal.terms} target="_blank" rel="noreferrer">
            Terms of Service
          </a>{" "}
          and{" "}
          <a href={props.legal.privacy} target="_blank" rel="noreferrer">
            Privacy Policy
          </a>
        </span>
      </label>
      {problem && <p className="signin-error" role="alert">{problem}</p>}
      {/* Without the tick the button does not work, and neither does Enter
          in the name: a form whose button is disabled is not sent. */}
      <button type="submit" className="button-accent big wide"
        disabled={busy || !name.trim() || !agree}>
        {busy ? "Creating…" : "Create account"}
      </button>
      <div className="signin-links single">
        <button type="button" className="link-button" disabled={busy}
          onClick={() => props.onBack(null)}>
          Use another email
        </button>
      </div>
    </form>
  );
}

/** A quick code for this device: typed once, then once more on the next
 * screen. */
function OfferStep(props: { name: string; fresh: boolean; onDone: () => void }) {
  /** The code typed the first time, while it is typed again. */
  const [first, setFirst] = useState<string | null>(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [wrong, setWrong] = useState(0);

  const startOver = (why: string) => {
    setFirst(null);
    setPin("");
    setProblem(why);
    setWrong((n) => n + 1);
  };

  const typed = async (value: string) => {
    setPin(value);
    if (value.length < PIN_DIGITS) return;
    if (first === null) {
      if (tooEasy(value)) {
        startOver("Too easy to guess: choose digits that are not all the " +
          "same and not in a row.");
        return;
      }
      setProblem(null);
      setFirst(value);
      setPin("");
      return;
    }
    if (value !== first) {
      startOver("The codes did not match. Create it again.");
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      await setQuickCode(value);
      props.onDone();
    } catch (error) {
      setBusy(false);
      startOver(textOf(error));
    }
  };

  const again = first !== null;
  const greeting = props.fresh ? `Welcome, ${props.name}!` : `Hello, ${props.name}!`;
  return (
    <form className="signin-form" onSubmit={(e) => e.preventDefault()}>
      {/* A new screen for the second time, the same input under it: the
          keyboard of a phone stays open. */}
      <h1 key={again ? "again" : "first"}>{again ? "Repeat the code" : greeting}</h1>
      <p className="signin-lead" aria-live="polite">
        {again ? "Enter the same four digits once more"
          : "Create a quick code for this device"}
      </p>
      <Slots count={PIN_DIGITS} secret value={pin}
        label={again ? "The quick code, once more" : "A quick code"}
        name={again ? "pin-again" : "pin"} autoComplete="off" busy={busy}
        wrong={wrong} onChange={(value) => void typed(value)} />
      {problem && <p className="signin-error" role="alert">{problem}</p>}
      <div className="signin-links single">
        <button type="button" className="link-button quiet" disabled={busy}
          onClick={props.onDone}>
          Not now
        </button>
      </div>
    </form>
  );
}

function PinStep(props: {
  device: KnownDevice;
  onIn: () => void;
  onCode: (email: string) => void;
  onEmail: (why: string | null) => void;
  onSwitch: () => void;
}) {
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [wrong, setWrong] = useState(0);
  const checking = useRef(false);
  const first = props.device.name.split(/\s+/)[0] || props.device.name;

  const check = async (value: string) => {
    if (checking.current) return;
    checking.current = true;
    setBusy(true);
    setProblem(null);
    try {
      await quickSignIn(value);
      props.onIn();
    } catch (error) {
      if (error instanceof Refused && error.status === 410) {
        props.onEmail(error.message);
        return;
      }
      setPin("");
      setProblem(textOf(error));
      setWrong((n) => n + 1);
      setBusy(false);
      checking.current = false;
    }
  };

  /** A code from email to the device's address, at once. */
  const forgot = async () => {
    setAsking(true);
    setProblem(null);
    try {
      const sent = await askCode(props.device.email);
      props.onCode(sent.email);
    } catch (error) {
      setProblem(textOf(error));
      setAsking(false);
    }
  };

  return (
    <form className="signin-form" onSubmit={(e) => {
      e.preventDefault();
      if (pin.length === PIN_DIGITS) void check(pin);
    }}>
      <h1>Welcome back, {first}!</h1>
      <p className="signin-lead">Enter your quick code</p>
      <Slots count={PIN_DIGITS} secret value={pin} label="Quick code" name="pin"
        autoComplete="off" busy={busy} wrong={wrong}
        onChange={(value) => {
          setPin(value);
          if (value.length === PIN_DIGITS) void check(value);
        }} />
      {problem && <p className="signin-error" role="alert">{problem}</p>}
      <div className="signin-links">
        <button type="button" className="link-button" disabled={asking || busy}
          onClick={() => void forgot()}>
          Forgot the code?
        </button>
        <button type="button" className="link-button" disabled={busy}
          onClick={props.onSwitch}>
          Switch account
        </button>
      </div>
    </form>
  );
}
