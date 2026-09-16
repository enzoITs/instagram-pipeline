import { useCallback, useEffect, useState } from "react";

import { AlertIcon } from "../../components/Icons";
import "../../styles/Accounts.css";
import type {
  Account,
  AccountSummary,
  CollectionRun,
  Health,
  MediaBreakdown,
  MediaItem,
  MediaPage,
  TimeseriesPoint,
} from "../../types";
import {
  connectAccountUrl,
  disconnectAccount,
  exportCsvUrl,
  followRun,
  listAccounts,
  listMedia,
  listRuns,
  readBreakdown,
  readSummary,
  readTimeseries,
  startCollection,
} from "../../utils/accountsApi";
import { formatRelative } from "../../utils/format";
import { toApiError } from "../../utils/http";
import BreakdownChart from "./BreakdownChart";
import MediaDialog from "./MediaDialog";
import { parseSort } from "./labels";
import MediaTable from "./MediaTable";
import RunsList from "./RunsList";
import SummaryCards from "./SummaryCards";
import TimeseriesChart from "./TimeseriesChart";

const PAGE_SIZE = 25;

interface Props {
  health: Health | null;
  /** Hands a post's permalink to the analysis tab. */
  onAnalyze: (url: string) => void;
  notice?: { kind: "success" | "error"; message: string } | null;
  onDismissNotice?: () => void;
}

export default function AccountsPage({ health, onAnalyze, notice, onDismissNotice }: Props) {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [summary, setSummary] = useState<AccountSummary | null>(null);
  const [points, setPoints] = useState<TimeseriesPoint[]>([]);
  const [days, setDays] = useState(90);
  const [breakdown, setBreakdown] = useState<MediaBreakdown[]>([]);
  const [page, setPage] = useState<MediaPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [mediaProductType, setMediaProductType] = useState("");
  const [sort, setSort] = useState("timestamp:desc");
  const [runs, setRuns] = useState<CollectionRun[]>([]);
  const [openMedia, setOpenMedia] = useState<MediaItem | null>(null);
  const [collecting, setCollecting] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [loadingMedia, setLoadingMedia] = useState(false);

  const account = accounts?.find((item) => item.id === accountId) ?? null;

  const loadAccounts = useCallback(async (signal?: AbortSignal) => {
    try {
      const rows = await listAccounts(signal);
      setAccounts(rows);
      setAccountId((current) => current ?? rows[0]?.id ?? null);
      return rows;
    } catch (reason) {
      const error = toApiError(reason);
      if (error.code !== "cancelled") setMessage({ kind: "error", text: error.message });
      setAccounts([]);
      return [];
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    // Fetch-on-mount: state is written when the request resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadAccounts(controller.signal);
    return () => controller.abort();
  }, [loadAccounts]);

  // Headline numbers, charts and run history for the selected account.
  useEffect(() => {
    if (accountId === null) return;
    const controller = new AbortController();
    Promise.all([
      readSummary(accountId, controller.signal).then(setSummary),
      readTimeseries(accountId, days, controller.signal).then(setPoints),
      readBreakdown(accountId, controller.signal).then(setBreakdown),
      listRuns(accountId, 10, controller.signal).then(setRuns),
    ]).catch((reason) => {
      const error = toApiError(reason);
      if (error.code !== "cancelled") setMessage({ kind: "error", text: error.message });
    });
    return () => controller.abort();
  }, [accountId, days]);

  // The post table has its own filters, so it reloads on its own.
  useEffect(() => {
    if (accountId === null) return;
    const controller = new AbortController();
    // The table shows a "loading" row while the filters' request is in flight.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoadingMedia(true);
    listMedia(
      accountId,
      { limit: PAGE_SIZE, offset, search, mediaProductType, ...parseSort(sort) },
      controller.signal,
    )
      .then(setPage)
      .catch((reason) => {
        const error = toApiError(reason);
        if (error.code !== "cancelled") setMessage({ kind: "error", text: error.message });
      })
      .finally(() => setLoadingMedia(false));
    return () => controller.abort();
  }, [accountId, offset, search, mediaProductType, sort]);

  const refresh = useCallback(() => {
    setOffset(0);
    setDays((value) => value); // triggers nothing by itself; the effects below re-run on accountId
    if (accountId !== null) {
      void readSummary(accountId).then(setSummary);
      void readTimeseries(accountId, days).then(setPoints);
      void readBreakdown(accountId).then(setBreakdown);
      void listRuns(accountId, 10).then(setRuns);
      void listMedia(accountId, {
        limit: PAGE_SIZE,
        offset: 0,
        search,
        mediaProductType,
        ...parseSort(sort),
      }).then(setPage);
    }
  }, [accountId, days, search, mediaProductType, sort]);

  const collect = async () => {
    if (accountId === null) return;
    setCollecting("Coletando…");
    setMessage(null);
    try {
      const started = await startCollection(accountId);
      const run = await followRun(started.run_id, {
        onProgress: (current) => setCollecting(`Coletando… ${current.media_seen} posts`),
      });
      if (run.status === "failed") {
        setMessage({ kind: "error", text: `A coleta falhou: ${run.error ?? "motivo desconhecido"}` });
      } else if (run.status === "running") {
        setMessage({
          kind: "error",
          text: "A coleta ainda está em andamento no servidor. Recarregue em alguns minutos.",
        });
      } else {
        const suffix = run.status === "partial" ? ` com avisos (${run.error})` : "";
        setMessage({
          kind: "success",
          text: `Coleta concluída: ${run.snapshots_created} posts atualizados em ${run.api_calls} chamadas${suffix}.`,
        });
      }
      await loadAccounts();
      refresh();
    } catch (reason) {
      setMessage({ kind: "error", text: toApiError(reason).message });
    } finally {
      setCollecting(null);
    }
  };

  const disconnect = async () => {
    if (account === null) return;
    try {
      await disconnectAccount(account.id);
      setMessage({ kind: "success", text: `@${account.username ?? account.ig_user_id} foi desconectada.` });
      await loadAccounts();
    } catch (reason) {
      setMessage({ kind: "error", text: toApiError(reason).message });
    }
  };

  const analyzeMedia = (media: MediaItem) => {
    setOpenMedia(null);
    if (media.permalink) onAnalyze(media.permalink);
    else setMessage({ kind: "error", text: "Este post não tem link público para analisar." });
  };

  if (accounts === null) return <p className="empty">Carregando contas…</p>;

  if (accounts.length === 0) {
    return (
      <section className="accounts__empty" aria-labelledby="no-accounts">
        <h2 id="no-accounts">Nenhuma conta conectada</h2>
        <p>
          Conecte uma conta Instagram Business ou Creator para coletar métricas oficiais (alcance,
          salvamentos e compartilhamentos) e guardar o histórico que a Meta não mantém.
        </p>
        {health?.meta_configured === false && (
          <p className="notice notice--account">
            <AlertIcon size={18} />
            INSTAGRAM_APP_ID e INSTAGRAM_APP_SECRET ainda não estão configurados: o botão abaixo vai falhar
            até que sejam preenchidos. Veja o SETUP.md.
          </p>
        )}
        <a className="button button--primary" href={connectAccountUrl()}>
          Conectar conta do Instagram
        </a>
      </section>
    );
  }

  return (
    <div className="accounts">
      {notice && (
        <p className={`notice notice--account notice--${notice.kind}`} role="status">
          <AlertIcon size={18} />
          {notice.message}
          {onDismissNotice && (
            <button type="button" className="link-button" onClick={onDismissNotice}>
              Fechar
            </button>
          )}
        </p>
      )}
      {message && (
        <p className={`notice notice--account notice--${message.kind}`} role="status">
          <AlertIcon size={18} />
          {message.text}
        </p>
      )}
      {account && !account.is_active && (
        <p className="notice notice--account notice--error" role="status">
          <AlertIcon size={18} />
          Esta conta está desconectada (token inválido ou revogado). Reconecte-a para voltar a coletar.
        </p>
      )}

      <div className="accounts__bar">
        <label className="accounts__select">
          Conta
          <select
            value={accountId ?? ""}
            onChange={(event) => {
              setAccountId(Number(event.target.value));
              setOffset(0);
            }}
          >
            {accounts.map((item) => (
              <option key={item.id} value={item.id}>
                {item.username ? `@${item.username}` : item.ig_user_id}
                {item.is_active ? "" : " (desconectada)"}
              </option>
            ))}
          </select>
        </label>
        <span className="accounts__bar-spacer" />
        {health?.next_collection_at && (
          <span className="summary-card__note">
            Próxima coleta automática {formatRelative(health.next_collection_at)}
          </span>
        )}
        <button
          type="button"
          className="button button--primary"
          onClick={collect}
          disabled={collecting !== null || !account?.is_active}
        >
          {collecting ?? "Coletar agora"}
        </button>
        {accountId !== null && (
          <a className="button button--ghost" href={exportCsvUrl(accountId)}>
            Exportar CSV
          </a>
        )}
        <a className="button button--ghost" href={connectAccountUrl()}>
          Conectar outra
        </a>
        <button type="button" className="button button--ghost" onClick={disconnect}>
          Desconectar
        </button>
      </div>

      {summary && <SummaryCards summary={summary} />}

      <TimeseriesChart points={points} days={days} onDaysChange={setDays} />
      <BreakdownChart rows={breakdown} />

      <MediaTable
        items={page?.items ?? []}
        total={page?.total ?? 0}
        offset={offset}
        pageSize={PAGE_SIZE}
        loading={loadingMedia}
        search={search}
        mediaProductType={mediaProductType}
        sort={sort}
        onSearchChange={(value) => {
          setSearch(value);
          setOffset(0);
        }}
        onTypeChange={(value) => {
          setMediaProductType(value);
          setOffset(0);
        }}
        onSortChange={(value) => {
          setSort(value);
          setOffset(0);
        }}
        onPageChange={setOffset}
        onOpenHistory={setOpenMedia}
        onAnalyze={analyzeMedia}
      />

      <RunsList runs={runs} />

      {openMedia && (
        <MediaDialog media={openMedia} onClose={() => setOpenMedia(null)} onAnalyze={analyzeMedia} />
      )}
    </div>
  );
}
