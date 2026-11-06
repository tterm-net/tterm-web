// /connect-ai (0.1.29): an AI app (Claude, ChatGPT, Claude Code, Cursor, any
// with MCP) opened this page in the browser to connect to tTerm, through
// /oauth/authorize. As on the approved canvas (version 121, McpAllow): who
// asks and where it takes the person back to, the machines with their
// terminals and their log, what the AI may do, how long; Allow takes the
// person back to the app with a code, once, that only the app can use.
// Not signed in, the usual sign-in comes first; a session older than a
// quarter of an hour confirms first, as for tTerm for Mac (/connect): an AI
// allowed stays connected.

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AccountChanged, AiRequestOver, allowAi, ConfirmFirst, CoreError, denyAi, endSession,
  loadAiRequest, loadState, SignedOut, type AiCan, type AiRequest,
} from "./api";
import {
  AiChoice, AiKeys, allTicked, CAN_AT_FIRST, choiceSummary, lockedNames, tickedMachines,
  type ChoiceProject, type ChoiceTerm, type Ticks,
} from "./AiChoice";
import { termUntil } from "./aiText";
import { languageOf, showLanguage, useT } from "./i18n";
import { Logo, Spark, Warning } from "./icons";
import { SignIn } from "./SignIn";
import { applyTheme } from "./theme";

type View =
  | { kind: "loading" }
  | { kind: "signin" }
  | { kind: "ask"; asked: AiRequest }
  | { kind: "going"; asked: AiRequest; summary: string }
  | { kind: "problem"; title: string; text: string };

const SUPPORT = "support@tterm.net";

/** This device's time zone: the times the AI reads are in it. */
function zone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function ConnectAi() {
  const t = useT();
  const query = useMemo(() => new URLSearchParams(location.search), []);
  const requestId = query.get("request");
  const problemCode = query.get("problem");
  const [view, setView] = useState<View>({ kind: "loading" });
  const [project, setProject] = useState("");
  const [ticks, setTicks] = useState<Ticks>({});
  const [seen, setSeen] = useState<Set<string>>(new Set());
  const [term, setTerm] = useState<ChoiceTerm>("forever");
  const [can, setCan] = useState<AiCan | null>(null);
  const [proof, setProof] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  /** Allow was pressed with a session too old for it, by this person: signed
   * in again as them, the page allows by itself (as /connect). */
  const [confirming, setConfirming] = useState<string | null>(null);
  const [another, setAnother] = useState(false);

  const projects: ChoiceProject[] = useMemo(() => view.kind === "ask" || view.kind === "going"
    ? view.asked.projects.map((p) => ({ id: p.id, name: p.name,
      machines: p.machines.map((m) => ({ id: m.id, name: m.name, kind: m.kind,
        method: m.method, guarded: m.guarded })) }))
    : [], [view]);

  const over = (text: string) => setView({ kind: "problem", title: t("connect_ai.over_title"),
    text: text || t("connect_ai.over") });

  /** The request, for whoever is signed in; with `thenAllowFor`, allowed at
   * once if that is still the person who pressed Allow. */
  const load = (thenAllowFor: string | null = null) => {
    if (!requestId) return;
    loadState(true)
      .then((state) => {
        applyTheme(state.me.theme);
        void showLanguage(languageOf(state.me));
        return loadAiRequest(requestId);
      })
      .then((asked) => {
        const first = asked.same?.machines.length ? asked.same.machines[0].projectId
          : asked.projects[0]?.id ?? "";
        setProject((was) => was || first);
        setTicks((was) => {
          if (Object.keys(was).length) return was;
          if (asked.same) {
            const kept: Ticks = {};
            for (const m of asked.same.machines) kept[m.id] = [m.terminals, m.log];
            return kept;
          }
          return allTicked(asked.projects.find((p) => p.id === first));
        });
        setSeen((was) => (was.size ? was : new Set([first])));
        // What it may do: as the same app's latest connection had it, or
        // as at first (0.1.30).
        setCan((was) => was ?? (asked.same ? { ...asked.same.can, see: true } : CAN_AT_FIRST));
        setView({ kind: "ask", asked });
        if (thenAllowFor !== null && asked.me.id === thenAllowFor) void allowNow(asked);
      })
      .catch((error) => {
        if (error instanceof SignedOut) setView({ kind: "signin" });
        else if (error instanceof AiRequestOver) over("");
        else setView({ kind: "problem", title: t("connect.no_connection"),
          text: t("connect.no_answer") });
      });
  };

  useEffect(() => {
    document.title = `${t("connect_ai.title_tab")} · tTerm`;
    if (problemCode || !requestId) {
      setView({ kind: "problem", title: t("connect.incomplete"),
        text: problemCode === "client" ? t("connect_ai.problem_client")
          : problemCode === "redirect" ? t("connect_ai.problem_redirect")
            : t("connect_ai.problem_request") });
      return;
    }
    load();
  }, []);

  const allowNow = async (asked: AiRequest) => {
    setBusy(true);
    setProblem(null);
    try {
      const summary = choiceSummary(asked.request.name, projects.length ? projects
        : asked.projects.map((p) => ({ id: p.id, name: p.name, machines: p.machines.map((m) => ({
          id: m.id, name: m.name, kind: m.kind, method: m.method, guarded: m.guarded })) })),
      ticks, term === "keep" ? null : termUntil(term), t);
      const { redirect } = await allowAi(asked.request.id, {
        machines: tickedMachines(ticks), can: can ?? CAN_AT_FIRST, language: t.lang,
        term, zone: zone(), account: asked.me.id, ...(proof ? { auth: proof } : {}),
      });
      setView({ kind: "going", asked, summary });
      location.assign(redirect);
    } catch (error) {
      setBusy(false);
      if (error instanceof ConfirmFirst) {
        await endSession().catch(() => {});
        setConfirming(asked.me.id);
        setView({ kind: "signin" });
      } else if (error instanceof SignedOut) {
        setView({ kind: "signin" });
      } else if (error instanceof AccountChanged) {
        setProblem(t("connect_ai.account_changed"));
        load();
      } else if (error instanceof AiRequestOver) {
        over("");
      } else if (error instanceof CoreError && error.status === 423) {
        setProblem(t("connect_ai.keys_first"));
      } else {
        setProblem((error as Error).message);
      }
    }
  };

  if (view.kind === "signin") {
    const app = t("connect_ai.the_ai");
    return (
      <SignIn note={confirming !== null ? t("connect_ai.confirm", { app })
        : another ? t("connect_ai.signin_other", { app })
        : t("connect_ai.signin_then", { app })}
        another={another}
        onSignedIn={() => {
          const after = confirming;
          setConfirming(null);
          setAnother(false);
          load(after);
        }} />
    );
  }
  if (view.kind === "loading") return <Frame busy />;
  if (view.kind === "problem") {
    return (
      <Frame>
        <div className="signin-form connect-form ai-connect-problem">
          <span className="connect-icon" aria-hidden="true"><Spark size={22} /></span>
          <h1>{view.title}</h1>
          <p className="signin-lead connect-lead">{view.text}</p>
        </div>
      </Frame>
    );
  }
  const { asked } = view;
  const ai = asked.request.name;
  if (view.kind === "going") {
    return (
      <Frame wide>
        <div className="signin-form connect-form" role="status">
          <span className="connect-icon ok" aria-hidden="true"><Spark size={22} /></span>
          <h1>{t("connect_ai.back", { ai })}</h1>
          <p className="signin-lead connect-lead">
            {view.summary} {t("connect_ai.back_note")}
          </p>
          {asked.request.place.kind !== "web" && (
            <p className="signin-small">{t("connect_ai.back_app", { ai })}</p>
          )}
        </div>
      </Frame>
    );
  }

  const locked = lockedNames(projects, ticks);
  const keysNeeded = locked.length > 0 && asked.keys.guarding && !asked.keys.open && !proof;
  const ticked = tickedMachines(ticks).length;
  const why = !ticked ? t("connect_ai.why_none")
    : keysNeeded ? t("connect_ai.why_keys") : "";
  const until = term === "keep" ? null : termUntil(term);
  const place = asked.request.place;
  const where = place.kind === "web" ? place.host ?? ""
    : place.kind === "local" ? t("connect_ai.place_here") : t("connect_ai.place_app",
      { scheme: place.scheme ?? "" });

  const pickProject = (id: string) => {
    setProject(id);
    if (!seen.has(id) && !asked.same) {
      // Shown for the first time: its machines ticked, as at first.
      setTicks((was) => ({ ...allTicked(asked.projects.find((p) => p.id === id)), ...was }));
    }
    setSeen((was) => new Set([...was, id]));
  };

  const cancel = async () => {
    setBusy(true);
    try {
      const { redirect } = await denyAi(asked.request.id);
      location.assign(redirect);
    } catch (error) {
      setBusy(false);
      if (error instanceof AiRequestOver || (error instanceof CoreError && error.status === 410)) {
        over("");
      } else {
        setProblem((error as Error).message);
      }
    }
  };

  const useAnother = async () => {
    setBusy(true);
    setProblem(null);
    try {
      await endSession();
    } catch (error) {
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

  return (
    <Frame wide>
      <div className="signin-form connect-form ai-connect">
        <span className="connect-icon" aria-hidden="true"><Spark size={22} /></span>
        <h1>{t("connect_ai.title", { ai })}</h1>
        <p className="signin-lead connect-lead">
          {t.rich(place.kind === "web" ? "connect_ai.asks_web" : "connect_ai.asks_here", {
            ai, email: asked.me.email ?? asked.me.name, place: where,
            b: (inner: ReactNode) => <strong className="ink">{inner}</strong>,
          })}
          {" "}
          <button type="button" className="ai-link" disabled={busy}
            onClick={() => void useAnother()}>
            {t("connect_ai.not_you")}
          </button>
        </p>
        {asked.request.warning && (
          <p className="ai-warning" role="alert">
            <Warning size={16} />
            <span>{t("connect_ai.warning", { claims: asked.request.warning.claims,
              real: asked.request.warning.real, host: asked.request.warning.host })}</span>
          </p>
        )}
        {asked.request.said && asked.request.said !== ai && (
          <p className="ai-small ai-said">{t("connect_ai.said", { said: asked.request.said })}</p>
        )}
        <AiChoice ai={ai} projects={projects} project={project} onProject={pickProject}
          ticks={ticks} onTicks={setTicks} term={term} onTerm={setTerm} disabled={busy}
          can={can ?? CAN_AT_FIRST} onCan={setCan}
          idPrefix="ca" locks={asked.keys.guarding}
          keys={asked.keys.guarding ? (
            <AiKeys ai={ai} locked={locked} open={asked.keys.open} proof={proof}
              password={asked.keys.password} onProof={setProof} idPrefix="ca"
              onCheck={() => load()} />
          ) : null} />
        <div className="ai-summary">
          <p className="ai-summary-main">
            {why ? t("ai.summary_none") : choiceSummary(ai, projects, ticks, until, t)}
          </p>
          <p className="ai-small">
            {place.kind === "web" ? t("connect_ai.goes_to", { ai, place: where })
              : t("connect_ai.goes_to_here", { ai })}
            {" "}{t("connect_ai.forget_any_time", { ai })}
          </p>
        </div>
        <div className="ai-actions">
          {why && <span className="ai-why" role="status">{why}</span>}
          <button type="button" className="button-plain" disabled={busy}
            onClick={() => void cancel()}>
            {t("common.cancel")}
          </button>
          <button type="button" className="button-accent big" disabled={busy || !!why}
            onClick={() => void allowNow(asked)}>
            {t("connect.allow")}
          </button>
        </div>
        {problem && <div className="error-box" role="alert">{problem}</div>}
      </div>
    </Frame>
  );
}

function Frame(props: { children?: ReactNode; busy?: boolean; wide?: boolean }) {
  return (
    <main className="signin" aria-busy={props.busy || undefined}>
      <header className="signin-brand">
        <Logo size={36} />
        <span aria-hidden="true">tTerm</span>
      </header>
      <div className="signin-middle">
        {props.children && (
          <section className={props.wide ? "signin-card ai-card" : "signin-card"}>
            {props.children}
          </section>
        )}
      </div>
      <footer className="signin-footer">
        <a href={`mailto:${SUPPORT}`}>{SUPPORT}</a>
      </footer>
    </main>
  );
}
