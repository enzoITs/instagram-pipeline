import { useCallback, useEffect, useState } from "react";

/**
 * The smallest router this app needs: two sections in the hash, so the Back
 * button works and a link can point straight at one analysis.
 *
 *   #/contas             connected accounts
 *   #/analise            link analysis (form)
 *   #/analise/{videoId}  one analysis
 */
export type Tab = "contas" | "analise";

export interface Route {
  tab: Tab;
  videoId: string | null;
}

export const TAB_LABELS: Record<Tab, string> = {
  contas: "Minhas contas",
  analise: "Análise por link",
};

export function parseRoute(hash: string): Route {
  const [, tab = "", videoId = ""] = hash.replace(/^#\/?/, "/").split("/");
  const name: Tab = tab === "analise" ? "analise" : "contas";
  return { tab: name, videoId: name === "analise" && videoId ? decodeURIComponent(videoId) : null };
}

export function routeToHash(route: Route): string {
  if (route.tab === "analise" && route.videoId) return `#/analise/${encodeURIComponent(route.videoId)}`;
  return `#/${route.tab}`;
}

export function useRoute(): [Route, (route: Route, replace?: boolean) => void] {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  useEffect(() => {
    const update = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  const navigate = useCallback((next: Route, replace = false) => {
    const hash = routeToHash(next);
    const url = new URL(window.location.href);
    url.hash = hash;
    // pushState keeps Back working between the two tabs; replace is for
    // redirects the user did not ask for (a finished analysis, a failed load).
    if (replace) window.history.replaceState(null, "", url);
    else window.history.pushState(null, "", url);
    setRoute(parseRoute(hash));
  }, []);

  return [route, navigate];
}
