import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";

import AnalysisForm from "./components/AnalysisForm";
import ErrorBoundary from "./components/ErrorBoundary";
import { LogoMark, MoonIcon, SunIcon } from "./components/Icons";
import LoadingSpinner from "./components/LoadingSpinner";
import LoginPage from "./components/LoginPage";
import RecentAnalyses from "./components/RecentAnalyses";
import AccountsPage from "./features/accounts/AccountsPage";
import type { AnalysisData, Health, RecentItem } from "./types";
import { readHealth, readSession, signOut } from "./utils/accountsApi";
import {
  analyzeVideo,
  AnalysisFailedError,
  deleteAnalysis,
  getAnalysis,
  pollAnalysis,
  PollTimeoutError,
  setTracking,
  toApiError,
} from "./utils/api";
import { setUnauthorizedHandler } from "./utils/http";
import { TAB_LABELS, useRoute, type Tab } from "./utils/router";
import { useTheme } from "./utils/theme";

// Charts (Recharts) load only when a result is shown, keeping the first page light.
const Dashboard = lazy(() => import("./components/Dashboard"));

type View =
  | { name: "form"; error?: string; retryUrl?: string; timedOutVideoId?: string }
  | { name: "submitting"; url: string }
  | {
      name: "loading";
      videoId: string;
      target: string | null;
      step: number;
      totalSteps?: number;
      startedAt: number;
    }
  | { name: "dashboard"; data: AnalysisData; cached: boolean };

type Auth = "checking" | "required" | "signed-in";

/** ?connected / ?error come back from the Meta OAuth callback. */
function readCallbackNotice(): { kind: "success" | "error"; message: string } | null {
  const params = new URLSearchParams(window.location.search);
  const connected = params.get("connected");
  const error = params.get("error");
  if (!connected && !error) return null;
  const url = new URL(window.location.href);
  url.search = "";
  window.history.replaceState(null, "", url);
  return connected
    ? { kind: "success", message: "Conta conectada. Clique em “Coletar agora” para a primeira coleta." }
    : { kind: "error", message: `Falha ao conectar a conta: ${error}` };
}

export default function App() {
  const [route, navigate] = useRoute();
  const [auth, setAuth] = useState<Auth>("checking");
  const [authRequired, setAuthRequired] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [notice, setNotice] = useState(readCallbackNotice);
  const [view, setView] = useState<View>(() =>
    route.videoId
      ? { name: "loading", videoId: route.videoId, target: null, step: 0, startedAt: Date.now() }
      : { name: "form" },
  );
  const [recentKey, setRecentKey] = useState(0);
  const [tracking, setTrackingBusy] = useState(false);
  const [theme, setTheme] = useTheme();
  const pollingVideoId = view.name === "loading" ? view.videoId : null;
  const lastUrl = useRef<string | undefined>(undefined);

  // An expired session must not leave half-loaded panels on screen.
  useEffect(() => {
    setUnauthorizedHandler(() => setAuth("required"));
    return () => setUnauthorizedHandler(null);
  }, []);

  const loadSession = useCallback(async () => {
    try {
      const session = await readSession();
      setAuthRequired(session.auth_required);
      setAuth(session.authenticated ? "signed-in" : "required");
      if (session.authenticated) setHealth(await readHealth().catch(() => null));
    } catch {
      // The API is unreachable; show the login rather than an empty dashboard.
      setAuth("required");
    }
  }, []);

  useEffect(() => {
    // Fetch-on-mount: the state is written after the request resolves, which is
    // the documented exception to the "no setState in an effect" rule.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadSession();
  }, [loadSession]);

  // Poll whenever the analysis tab is showing a job; abort when leaving it.
  useEffect(() => {
    if (!pollingVideoId || auth !== "signed-in") return;
    const controller = new AbortController();
    pollAnalysis(pollingVideoId, {
      signal: controller.signal,
      onProgress: (progress) =>
        setView((current) =>
          current.name === "loading" && current.videoId === pollingVideoId
            ? { ...current, step: progress.step, totalSteps: progress.total_steps }
            : current,
        ),
    })
      .then((data) => setView({ name: "dashboard", data, cached: false }))
      .catch((reason) => {
        const error = toApiError(reason);
        if (error.code === "cancelled") return;
        navigate({ tab: "analise", videoId: null }, true);
        setRecentKey((key) => key + 1);
        if (error instanceof PollTimeoutError) {
          setView({ name: "form", error: error.message, timedOutVideoId: error.videoId });
        } else if (error instanceof AnalysisFailedError) {
          setView({ name: "form", error: error.message, retryUrl: lastUrl.current });
        } else {
          setView({ name: "form", error: error.message });
        }
      });
    return () => controller.abort();
  }, [pollingVideoId, auth, navigate]);

  const submit = useCallback(
    async (url: string) => {
      lastUrl.current = url;
      setView({ name: "submitting", url });
      try {
        const response = await analyzeVideo(url);
        if (response.status === "success") {
          navigate({ tab: "analise", videoId: response.video_id }, true);
          setView({ name: "dashboard", data: response.data, cached: true });
        } else {
          navigate({ tab: "analise", videoId: response.video_id }, true);
          setView({
            name: "loading",
            videoId: response.video_id,
            target: url,
            step: 0,
            startedAt: Date.now(),
          });
        }
      } catch (reason) {
        const error = toApiError(reason);
        const parts = [error.message];
        if (error.errorId) parts.push(`Código do erro: ${error.errorId}.`);
        // The rate-limit message already says when to retry.
        if (error.retryAfterSeconds && error.code !== "rate_limit_exceeded") {
          parts.push(`Tente de novo em ${error.retryAfterSeconds} s.`);
        }
        setView({ name: "form", error: parts.join(" "), retryUrl: url });
      }
    },
    [navigate],
  );

  /** "Analisar comentários" on a connected account's post. */
  const analyzeFromAccounts = useCallback(
    (url: string) => {
      navigate({ tab: "analise", videoId: null });
      void submit(url);
    },
    [navigate, submit],
  );

  const openRecent = useCallback(async (item: RecentItem) => {
    lastUrl.current = item.url ?? undefined;
    if (item.status !== "failed") {
      setView({ name: "loading", videoId: item.id, target: item.url, step: 0, startedAt: Date.now() });
      return;
    }
    try {
      const response = await getAnalysis(item.id);
      const message = response.status === "error" ? response.message : "A análise falhou.";
      setView({ name: "form", error: message, retryUrl: item.url ?? undefined });
    } catch (reason) {
      setView({ name: "form", error: toApiError(reason).message });
    }
  }, []);

  const goHome = useCallback(() => {
    navigate({ tab: "analise", videoId: null }, true);
    setRecentKey((key) => key + 1);
    setView({ name: "form" });
  }, [navigate]);

  const removeCurrent = useCallback(async () => {
    if (view.name !== "dashboard") return;
    try {
      await deleteAnalysis(view.data.video_id);
    } catch (reason) {
      throw toApiError(reason);
    }
    goHome();
  }, [view, goHome]);

  const toggleTracking = useCallback(
    async (tracked: boolean) => {
      if (view.name !== "dashboard") return;
      setTrackingBusy(true);
      try {
        const response = await setTracking(view.data.video_id, tracked);
        setView((current) =>
          current.name === "dashboard"
            ? { ...current, data: { ...current.data, tracked: response.tracked } }
            : current,
        );
      } catch {
        /* the toggle stays as it was; the next load shows the server's answer */
      } finally {
        setTrackingBusy(false);
      }
    },
    [view],
  );

  const leave = useCallback(async () => {
    try {
      await signOut();
    } finally {
      setAuth("required");
      setHealth(null);
    }
  }, []);

  const openTab = (tab: Tab) => navigate({ tab, videoId: null });

  if (auth === "checking") {
    return <p className="empty">Carregando…</p>;
  }

  if (auth === "required") {
    return (
      <div className="app">
        <main id="main" className="app-main">
          <LoginPage
            onSignedIn={() => {
              setAuth("signed-in");
              void loadSession();
            }}
          />
        </main>
      </div>
    );
  }

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Pular para o conteúdo
      </a>
      <header className="app-header">
        <button type="button" className="brand" onClick={() => openTab("contas")}>
          <LogoMark size={28} />
          <span>Instagram Pipeline</span>
        </button>
        <nav className="tabs" aria-label="Seções">
          {(Object.keys(TAB_LABELS) as Tab[]).map((tab) => (
            <button
              key={tab}
              type="button"
              className="tabs__link"
              aria-current={route.tab === tab ? "page" : undefined}
              onClick={() => openTab(tab)}
            >
              {TAB_LABELS[tab]}
            </button>
          ))}
        </nav>
        <div className="toolbar__actions">
          <button
            type="button"
            className="icon-button"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            aria-label={theme === "dark" ? "Usar tema claro" : "Usar tema escuro"}
            title={theme === "dark" ? "Usar tema claro" : "Usar tema escuro"}
          >
            {theme === "dark" ? <SunIcon /> : <MoonIcon />}
          </button>
          {authRequired && (
            <button type="button" className="button button--ghost" onClick={leave}>
              Sair
            </button>
          )}
        </div>
      </header>

      <main id="main" className="app-main">
        <ErrorBoundary onReset={goHome}>
          {route.tab === "contas" && (
            <AccountsPage
              health={health}
              onAnalyze={analyzeFromAccounts}
              notice={notice}
              onDismissNotice={() => setNotice(null)}
            />
          )}

          {route.tab === "analise" && (view.name === "form" || view.name === "submitting") && (
            <div className="home">
              <section className="hero" aria-labelledby="hero-title">
                <div className="hero__copy">
                  <h1 id="hero-title">O que estão dizendo nos seus comentários?</h1>
                  <p className="hero__lead">
                    Cole o link de um Reel ou post público. Em até dois minutos você vê quem quer comprar,
                    quais dúvidas se repetem e onde o tom ficou negativo. Se o post for de uma conta
                    conectada aqui, as métricas vêm da API oficial.
                  </p>
                  <AnalysisForm
                    key={view.name === "form" ? (view.retryUrl ?? "form") : "submitting"}
                    onSubmit={submit}
                    busy={view.name === "submitting"}
                    error={view.name === "form" ? view.error : null}
                    initialUrl={view.name === "form" ? view.retryUrl : view.url}
                  />
                  {view.name === "form" && view.timedOutVideoId && (
                    <button
                      type="button"
                      className="button button--ghost"
                      onClick={() =>
                        setView({
                          name: "loading",
                          videoId: view.timedOutVideoId!,
                          target: lastUrl.current ?? null,
                          step: 0,
                          startedAt: Date.now(),
                        })
                      }
                    >
                      Continuar acompanhando
                    </button>
                  )}
                </div>
                <figure
                  className="hero__example"
                  aria-label="Exemplo de como os comentários aparecem na análise"
                >
                  <figcaption>Exemplo de leitura</figcaption>
                  <ul>
                    <li>
                      <p>
                        Adorei! <mark className="marker">Quando</mark> <mark className="marker">vende</mark>?
                      </p>
                      <span className="tag tag--intent tag--potential_buyer">Quer comprar</span>
                    </li>
                    <li>
                      <p>
                        Alguém já <mark className="marker">testou</mark>?{" "}
                        <mark className="marker">Funciona</mark> mesmo?
                      </p>
                      <span className="tag tag--intent tag--question">Pergunta</span>
                    </li>
                    <li>
                      <p>
                        Veio com <mark className="marker">defeito</mark>, que{" "}
                        <mark className="marker">decepção</mark>
                      </p>
                      <span className="tag tag--intent tag--hater">Hater</span>
                    </li>
                  </ul>
                </figure>
              </section>
              <RecentAnalyses refreshKey={recentKey} onOpen={openRecent} />
            </div>
          )}

          {route.tab === "analise" && view.name === "loading" && (
            <LoadingSpinner
              step={view.step}
              totalSteps={view.totalSteps}
              target={view.target}
              startedAt={view.startedAt}
              onCancel={goHome}
            />
          )}

          {route.tab === "analise" && view.name === "dashboard" && (
            <Suspense fallback={<p className="empty">Montando o painel…</p>}>
              <Dashboard
                data={view.data}
                cached={view.cached}
                trackingBusy={tracking}
                onNewAnalysis={goHome}
                onDelete={removeCurrent}
                onToggleTracking={toggleTracking}
              />
            </Suspense>
          )}
        </ErrorBoundary>
      </main>
    </div>
  );
}
