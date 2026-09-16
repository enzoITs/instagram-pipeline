import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";

import AnalysisForm from "./components/AnalysisForm";
import ErrorBoundary from "./components/ErrorBoundary";
import { LogoMark, MoonIcon, SunIcon } from "./components/Icons";
import LoadingSpinner from "./components/LoadingSpinner";
import RecentAnalyses from "./components/RecentAnalyses";
import type { AnalysisData, RecentItem } from "./types";
import {
  analyzeVideo,
  AnalysisFailedError,
  deleteAnalysis,
  getAnalysis,
  pollAnalysis,
  PollTimeoutError,
  toApiError,
} from "./utils/api";
import { useTheme } from "./utils/theme";

// Charts (Recharts) load only when a result is shown, keeping the first page light.
const Dashboard = lazy(() => import("./components/Dashboard"));

type View =
  | { name: "form"; error?: string; retryUrl?: string; timedOutVideoId?: string }
  | { name: "submitting"; url: string }
  | { name: "loading"; videoId: string; target: string | null; step: number; startedAt: number }
  | { name: "dashboard"; data: AnalysisData; cached: boolean };

const PARAM = "analise";

function setAnalysisParam(videoId: string | null) {
  const url = new URL(window.location.href);
  if (videoId) url.searchParams.set(PARAM, videoId);
  else url.searchParams.delete(PARAM);
  window.history.replaceState(null, "", url);
}

export default function App() {
  const [view, setView] = useState<View>(() => {
    const videoId = new URLSearchParams(window.location.search).get(PARAM);
    return videoId
      ? { name: "loading", videoId, target: null, step: 0, startedAt: Date.now() }
      : { name: "form" };
  });
  const [recentKey, setRecentKey] = useState(0);
  const [theme, setTheme] = useTheme();
  const pollingVideoId = view.name === "loading" ? view.videoId : null;
  const lastUrl = useRef<string | undefined>(undefined);

  // Poll whenever we enter the loading view for a video; abort when leaving it.
  useEffect(() => {
    if (!pollingVideoId) return;
    const controller = new AbortController();
    setAnalysisParam(pollingVideoId);
    pollAnalysis(pollingVideoId, {
      signal: controller.signal,
      onProgress: (progress) =>
        setView((current) =>
          current.name === "loading" && current.videoId === pollingVideoId
            ? { ...current, step: progress.step }
            : current,
        ),
    })
      .then((data) => setView({ name: "dashboard", data, cached: false }))
      .catch((reason) => {
        const error = toApiError(reason);
        if (error.code === "cancelled") return;
        setAnalysisParam(null);
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
  }, [pollingVideoId]);

  const submit = useCallback(async (url: string) => {
    lastUrl.current = url;
    setView({ name: "submitting", url });
    try {
      const response = await analyzeVideo(url);
      if (response.status === "success") {
        setAnalysisParam(response.video_id);
        setView({ name: "dashboard", data: response.data, cached: true });
      } else {
        setView({ name: "loading", videoId: response.video_id, target: url, step: 0, startedAt: Date.now() });
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
  }, []);

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
    setAnalysisParam(null);
    setRecentKey((key) => key + 1);
    setView({ name: "form" });
  }, []);

  const removeCurrent = useCallback(async () => {
    if (view.name !== "dashboard") return;
    try {
      await deleteAnalysis(view.data.video_id);
    } catch (reason) {
      throw toApiError(reason);
    }
    goHome();
  }, [view, goHome]);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Pular para o conteúdo
      </a>
      <header className="app-header">
        <button type="button" className="brand" onClick={goHome}>
          <LogoMark size={28} />
          <span>Analisador de engajamento</span>
        </button>
        <button
          type="button"
          className="icon-button"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          aria-label={theme === "dark" ? "Usar tema claro" : "Usar tema escuro"}
          title={theme === "dark" ? "Usar tema claro" : "Usar tema escuro"}
        >
          {theme === "dark" ? <SunIcon /> : <MoonIcon />}
        </button>
      </header>

      <main id="main" className="app-main">
        <ErrorBoundary onReset={goHome}>
          {(view.name === "form" || view.name === "submitting") && (
            <div className="home">
              <section className="hero" aria-labelledby="hero-title">
                <div className="hero__copy">
                  <h1 id="hero-title">O que estão dizendo nos seus comentários?</h1>
                  <p className="hero__lead">
                    Cole o link de um Reel ou post público. Em até dois minutos você vê quem quer comprar,
                    quais dúvidas se repetem e onde o tom ficou negativo.
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

          {view.name === "loading" && (
            <LoadingSpinner
              step={view.step}
              target={view.target}
              startedAt={view.startedAt}
              onCancel={goHome}
            />
          )}

          {view.name === "dashboard" && (
            <Suspense fallback={<p className="empty">Montando o painel…</p>}>
              <Dashboard
                data={view.data}
                cached={view.cached}
                onNewAnalysis={goHome}
                onDelete={removeCurrent}
              />
            </Suspense>
          )}
        </ErrorBoundary>
      </main>
    </div>
  );
}
