/* Instagram Pipeline — dashboard front-end (vanilla JS, no build step). */
"use strict";

const state = {
  accounts: [],
  accountId: null,
  page: 0,
  pageSize: 25,
  total: 0,
  timeseries: [],
  charts: { timeseries: null, breakdown: null, media: null },
};

const el = (id) => document.getElementById(id);
const CHART_AVAILABLE = () => typeof window.Chart !== "undefined";

/* ------------------------------------------------------------------ utils */
function formatNumber(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("pt-BR").format(value);
}

function formatCompact(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function formatPercent(value) {
  if (value === null || value === undefined) return "—";
  return `${new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value)}%`;
}

function formatDate(value, withTime = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const options = withTime
    ? { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }
    : { day: "2-digit", month: "2-digit", year: "numeric" };
  return new Intl.DateTimeFormat("pt-BR", options).format(date);
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

function showBanner(message, kind = "ok", timeout = 0) {
  const banner = el("banner");
  banner.textContent = message;
  banner.className = `banner ${kind}`;
  banner.hidden = false;
  if (timeout) setTimeout(() => { banner.hidden = true; }, timeout);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let payload = null;
  const text = await response.text();
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = null; }
  }
  if (!response.ok) {
    const detail = (payload && (payload.detail || payload.message)) || `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload;
}

/* ------------------------------------------------------------- chart setup */
function chartDefaults() {
  if (!CHART_AVAILABLE()) return;
  window.Chart.defaults.color = "#96a0b3";
  window.Chart.defaults.font.family =
    'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
  window.Chart.defaults.borderColor = "#262d3a";
  window.Chart.defaults.maintainAspectRatio = false;
}

function destroyChart(key) {
  if (state.charts[key]) {
    state.charts[key].destroy();
    state.charts[key] = null;
  }
}

/* ------------------------------------------------------------------ boot */
async function boot() {
  chartDefaults();
  readUrlFlags();
  wireEvents();

  let health = null;
  try {
    health = await api("/api/health");
    el("footer-status").textContent =
      `${health.status} · ${health.login_flow} · Graph ${health.graph_api_version}` +
      (health.scheduler_running
        ? ` · próxima coleta ${formatDate(health.next_collection_at, true)}`
        : " · agendador desligado");
  } catch (error) {
    el("footer-status").textContent = `Falha ao contatar a API: ${error.message}`;
  }

  try {
    state.accounts = await api("/api/accounts");
  } catch (error) {
    showBanner(`Não foi possível carregar as contas: ${error.message}`, "error");
    return;
  }

  if (!state.accounts.length) {
    el("empty-state").hidden = false;
    el("btn-connect").hidden = false;
    if (health && !health.meta_configured) {
      const hint = el("empty-config");
      hint.textContent =
        "Aviso: INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET ainda não estão configurados no .env — " +
        "o botão de conectar vai falhar até que sejam preenchidos. Veja o SETUP.md.";
      hint.hidden = false;
      el("empty-actions").querySelector("a").classList.add("btn-sm");
    }
    return;
  }

  renderAccountSelect();
  const connect = el("btn-connect");
  connect.textContent = "Conectar outra conta";
  connect.classList.remove("btn-primary");
  connect.hidden = false;
  await selectAccount(state.accounts[0].id);
}

function readUrlFlags() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("connected")) {
    showBanner("Conta conectada com sucesso. Clique em “Coletar agora” para a primeira coleta.", "ok");
  }
  if (params.get("error")) {
    showBanner(`Falha ao conectar: ${params.get("error")}`, "error");
  }
  if (params.toString()) {
    window.history.replaceState({}, "", window.location.pathname);
  }
}

function wireEvents() {
  el("account-select").addEventListener("change", (event) => {
    selectAccount(Number(event.target.value));
  });
  el("btn-collect").addEventListener("click", collectNow);
  el("btn-export").addEventListener("click", () => {
    if (state.accountId) window.location.href = `/api/accounts/${state.accountId}/export.csv`;
  });
  el("range-select").addEventListener("change", loadTimeseries);
  el("metric-select").addEventListener("change", renderTimeseriesChart);
  el("type-select").addEventListener("change", () => { state.page = 0; loadMedia(); });
  el("sort-select").addEventListener("change", () => { state.page = 0; loadMedia(); });

  let searchTimer = null;
  el("search-input").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.page = 0; loadMedia(); }, 350);
  });

  el("prev-page").addEventListener("click", () => {
    if (state.page > 0) { state.page -= 1; loadMedia(); }
  });
  el("next-page").addEventListener("click", () => {
    if ((state.page + 1) * state.pageSize < state.total) { state.page += 1; loadMedia(); }
  });

  el("dialog-close").addEventListener("click", closeDialog);
  el("media-dialog").addEventListener("click", (event) => {
    if (event.target === el("media-dialog")) closeDialog();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDialog();
  });
}

function renderAccountSelect() {
  const select = el("account-select");
  select.innerHTML = state.accounts
    .map((account) => {
      const label = account.username ? `@${account.username}` : account.ig_user_id;
      const suffix = account.is_active ? "" : " (desconectada)";
      return `<option value="${account.id}">${escapeHtml(label + suffix)}</option>`;
    })
    .join("");
  select.hidden = state.accounts.length < 2;
  el("btn-collect").hidden = false;
  el("btn-export").hidden = false;
  el("dashboard").hidden = false;
}

async function selectAccount(accountId) {
  state.accountId = accountId;
  state.page = 0;
  el("account-select").value = String(accountId);
  await Promise.all([loadSummary(), loadTimeseries(), loadBreakdown(), loadMedia(), loadRuns()]);
}

/* --------------------------------------------------------------- summary */
async function loadSummary() {
  const grid = el("kpi-grid");
  try {
    const data = await api(`/api/accounts/${state.accountId}/summary`);
    const account = data.account;
    const cards = [
      { label: "Seguidores", value: formatNumber(account.followers_count) },
      { label: "Posts monitorados", value: formatNumber(data.tracked_media),
        sub: `${formatNumber(data.snapshots)} coletas no histórico` },
      { label: "Taxa de engajamento média", value: formatPercent(data.average_engagement_rate) },
      { label: "Alcance total", value: formatCompact(data.total_reach) },
      { label: "Visualizações", value: formatCompact(data.total_views) },
      { label: "Curtidas", value: formatCompact(data.total_likes) },
      { label: "Comentários", value: formatCompact(data.total_comments) },
      { label: "Salvamentos", value: formatCompact(data.total_saved) },
      { label: "Compartilhamentos", value: formatCompact(data.total_shares) },
      { label: "Última coleta", value: formatDate(data.last_collected_at, true) },
    ];
    grid.innerHTML = cards
      .map(
        (card) => `<div class="kpi">
            <div class="kpi-label">${escapeHtml(card.label)}</div>
            <div class="kpi-value">${escapeHtml(card.value)}</div>
            ${card.sub ? `<div class="kpi-sub">${escapeHtml(card.sub)}</div>` : ""}
          </div>`
      )
      .join("");

    if (!account.is_active) {
      showBanner(
        "Esta conta está desconectada (token inválido ou revogado). Reconecte-a para voltar a coletar.",
        "warn"
      );
    }
  } catch (error) {
    grid.innerHTML = `<p class="hint">Não foi possível carregar o resumo: ${escapeHtml(error.message)}</p>`;
  }
}

/* ------------------------------------------------------------ timeseries */
async function loadTimeseries() {
  const days = el("range-select").value;
  try {
    state.timeseries = await api(`/api/accounts/${state.accountId}/timeseries?days=${days}`);
  } catch (error) {
    state.timeseries = [];
    showBanner(`Falha ao carregar a série temporal: ${error.message}`, "error", 6000);
  }
  renderTimeseriesChart();
}

function renderTimeseriesChart() {
  const canvas = el("timeseries-chart");
  const empty = el("timeseries-empty");
  destroyChart("timeseries");

  if (!state.timeseries.length || !CHART_AVAILABLE()) {
    canvas.hidden = true;
    empty.hidden = false;
    empty.textContent = CHART_AVAILABLE()
      ? "Ainda não há coletas suficientes para desenhar o gráfico. Cada execução da coleta adiciona um ponto na linha do tempo."
      : "A biblioteca de gráficos (Chart.js via CDN) não pôde ser carregada. Os números continuam disponíveis nas tabelas.";
    return;
  }

  canvas.hidden = false;
  empty.hidden = true;

  const metric = el("metric-select").value;
  const label = el("metric-select").selectedOptions[0].textContent;
  const labels = state.timeseries.map((point) => formatDate(point.collected_at, true));
  const values = state.timeseries.map((point) => point[metric]);

  const context = canvas.getContext("2d");
  const gradient = context.createLinearGradient(0, 0, 0, 320);
  gradient.addColorStop(0, "rgba(238, 42, 123, 0.35)");
  gradient.addColorStop(1, "rgba(238, 42, 123, 0.02)");

  state.charts.timeseries = new window.Chart(context, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label,
          data: values,
          borderColor: "#ee2a7b",
          backgroundColor: gradient,
          borderWidth: 2,
          pointRadius: values.length > 40 ? 0 : 3,
          pointHoverRadius: 5,
          tension: 0.32,
          fill: true,
          spanGaps: true,
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (item) =>
              metric === "engagement_rate"
                ? `${label}: ${formatPercent(item.parsed.y)}`
                : `${label}: ${formatNumber(item.parsed.y)}`,
          },
        },
      },
      scales: {
        x: { grid: { display: false } },
        y: {
          beginAtZero: true,
          grid: { color: "#1f2532" },
          ticks: {
            callback: (value) =>
              metric === "engagement_rate" ? `${value}%` : formatCompact(value),
          },
        },
      },
    },
  });
}

/* ------------------------------------------------------------- breakdown */
async function loadBreakdown() {
  const empty = el("breakdown-empty");
  const canvas = el("breakdown-chart");
  destroyChart("breakdown");

  let rows = [];
  try {
    rows = await api(`/api/accounts/${state.accountId}/breakdown`);
  } catch {
    rows = [];
  }

  if (!rows.length || !CHART_AVAILABLE()) {
    canvas.hidden = true;
    empty.hidden = false;
    return;
  }
  canvas.hidden = false;
  empty.hidden = true;

  state.charts.breakdown = new window.Chart(canvas.getContext("2d"), {
    type: "bar",
    data: {
      labels: rows.map((row) => `${row.media_product_type} (${row.media_count})`),
      datasets: [
        {
          label: "Taxa de engajamento média (%)",
          data: rows.map((row) => row.average_engagement_rate ?? 0),
          backgroundColor: ["#ee2a7b", "#6228d7", "#f9ce34", "#35c48b", "#4a9df5"],
          borderRadius: 6,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false } },
        y: { beginAtZero: true, grid: { color: "#1f2532" }, ticks: { callback: (v) => `${v}%` } },
      },
    },
  });
}

/* ----------------------------------------------------------------- media */
async function loadMedia() {
  const body = el("media-body");
  const [orderBy, direction] = el("sort-select").value.split(":");
  const params = new URLSearchParams({
    limit: String(state.pageSize),
    offset: String(state.page * state.pageSize),
    order_by: orderBy,
    direction,
  });
  const type = el("type-select").value;
  const search = el("search-input").value.trim();
  if (type) params.set("media_product_type", type);
  if (search) params.set("search", search);

  try {
    const page = await api(`/api/accounts/${state.accountId}/media?${params}`);
    state.total = page.total;
    renderMediaRows(page.items);
    updatePager();
  } catch (error) {
    body.innerHTML = `<tr><td colspan="10" class="hint">Falha ao carregar posts: ${escapeHtml(error.message)}</td></tr>`;
  }
}

function renderMediaRows(items) {
  const body = el("media-body");
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="10" class="hint">
      Nenhum post coletado ainda. Clique em “Coletar agora” para buscar os dados na Meta.
    </td></tr>`;
    return;
  }

  body.innerHTML = items
    .map((item) => {
      const latest = item.latest || {};
      const caption = (item.caption || "").replace(/\s+/g, " ").slice(0, 90) || "(sem legenda)";
      const thumb = item.thumbnail_url || item.media_url;
      const permalink = item.permalink
        ? `<a class="post-link" href="${escapeHtml(item.permalink)}" target="_blank" rel="noopener">abrir no Instagram ↗</a>`
        : "";
      return `<tr>
        <td>
          <div class="post-cell">
            ${thumb ? `<img class="post-thumb" src="${escapeHtml(thumb)}" alt="" loading="lazy" onerror="this.remove()">` : ""}
            <div>
              <button class="linkish post-caption" data-media-id="${item.id}" title="Ver histórico">${escapeHtml(caption)}</button>
              <div>${permalink}</div>
            </div>
          </div>
        </td>
        <td><span class="pill">${escapeHtml(item.media_product_type || item.media_type || "—")}</span></td>
        <td>${formatDate(item.timestamp)}</td>
        <td class="num">${formatNumber(latest.likes)}</td>
        <td class="num">${formatNumber(latest.comments)}</td>
        <td class="num">${formatNumber(latest.saved)}</td>
        <td class="num">${formatNumber(latest.shares)}</td>
        <td class="num">${formatNumber(latest.reach)}</td>
        <td class="num">${formatNumber(latest.views)}</td>
        <td class="num">${formatPercent(latest.engagement_rate)}</td>
      </tr>`;
    })
    .join("");

  body.querySelectorAll("button[data-media-id]").forEach((button) => {
    button.addEventListener("click", () => openMediaDialog(Number(button.dataset.mediaId)));
  });
}

function updatePager() {
  const start = state.total === 0 ? 0 : state.page * state.pageSize + 1;
  const end = Math.min((state.page + 1) * state.pageSize, state.total);
  el("page-info").textContent = `${start}–${end} de ${formatNumber(state.total)}`;
  el("prev-page").disabled = state.page === 0;
  el("next-page").disabled = end >= state.total;
}

/* ------------------------------------------------------------------ runs */
async function loadRuns() {
  const body = el("runs-body");
  try {
    const runs = await api(`/api/runs?account_id=${state.accountId}&limit=10`);
    if (!runs.length) {
      body.innerHTML = `<tr><td colspan="5" class="hint">Nenhuma execução registrada ainda.</td></tr>`;
      return;
    }
    body.innerHTML = runs
      .map(
        (run) => `<tr title="${escapeHtml(run.error || "")}">
          <td>${formatDate(run.started_at, true)}</td>
          <td>${run.trigger === "scheduled" ? "agendada" : "manual"}</td>
          <td><span class="pill ${escapeHtml(run.status)}">${escapeHtml(run.status)}</span></td>
          <td class="num">${formatNumber(run.media_seen)}</td>
          <td class="num">${formatNumber(run.api_calls)}</td>
        </tr>`
      )
      .join("");
  } catch (error) {
    body.innerHTML = `<tr><td colspan="5" class="hint">${escapeHtml(error.message)}</td></tr>`;
  }
}

/* --------------------------------------------------------------- collect */
async function collectNow() {
  const button = el("btn-collect");
  button.disabled = true;
  button.textContent = "Coletando…";
  showBanner("Coletando métricas na Meta… isso pode levar alguns minutos para muitos posts.", "warn");

  try {
    const result = await api(`/api/accounts/${state.accountId}/collect`, { method: "POST" });
    if (result.status === "failed") {
      showBanner(`A coleta falhou: ${result.error}`, "error");
    } else if (result.status === "partial") {
      showBanner(
        `Coleta parcial: ${result.snapshots_created} posts salvos, com avisos — ${result.error}`,
        "warn"
      );
    } else {
      showBanner(
        `Coleta concluída: ${result.snapshots_created} posts atualizados em ${result.api_calls} chamadas à API.`,
        "ok",
        8000
      );
    }
    state.accounts = await api("/api/accounts");
    renderAccountSelect();
    await selectAccount(state.accountId);
  } catch (error) {
    showBanner(`A coleta falhou: ${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Coletar agora";
  }
}

/* ---------------------------------------------------------------- dialog */
async function openMediaDialog(mediaId) {
  const dialog = el("media-dialog");
  const body = el("dialog-body");
  body.innerHTML = `<p class="hint">Carregando histórico…</p>`;
  dialog.hidden = false;

  let detail;
  try {
    detail = await api(`/api/media/${mediaId}`);
  } catch (error) {
    body.innerHTML = `<p class="hint">Falha ao carregar: ${escapeHtml(error.message)}</p>`;
    return;
  }

  el("dialog-title").textContent =
    (detail.caption || "").replace(/\s+/g, " ").slice(0, 70) || "Histórico do post";

  const rows = detail.snapshots
    .slice()
    .reverse()
    .map(
      (snapshot) => `<tr>
        <td>${formatDate(snapshot.collected_at, true)}</td>
        <td class="num">${formatNumber(snapshot.likes)}</td>
        <td class="num">${formatNumber(snapshot.comments)}</td>
        <td class="num">${formatNumber(snapshot.saved)}</td>
        <td class="num">${formatNumber(snapshot.shares)}</td>
        <td class="num">${formatNumber(snapshot.reach)}</td>
        <td class="num">${formatNumber(snapshot.views)}</td>
        <td class="num">${formatPercent(snapshot.engagement_rate)}</td>
      </tr>`
    )
    .join("");

  body.innerHTML = `
    <p class="hint">
      ${escapeHtml(detail.media_product_type || detail.media_type || "—")} ·
      publicado em ${formatDate(detail.timestamp, true)} ·
      ${detail.snapshots.length} coleta(s)
      ${detail.permalink ? ` · <a class="post-link" href="${escapeHtml(detail.permalink)}" target="_blank" rel="noopener">abrir no Instagram ↗</a>` : ""}
    </p>
    <div class="chart-wrap chart-wrap-sm"><canvas id="media-chart"></canvas></div>
    <div class="table-scroll" style="margin-top:16px">
      <table class="table table-compact">
        <thead><tr>
          <th>Coleta</th><th class="num">Curtidas</th><th class="num">Coment.</th>
          <th class="num">Salv.</th><th class="num">Compart.</th><th class="num">Alcance</th>
          <th class="num">Views</th><th class="num">Engaj.</th>
        </tr></thead>
        <tbody>${rows || `<tr><td colspan="8" class="hint">Sem coletas.</td></tr>`}</tbody>
      </table>
    </div>`;

  destroyChart("media");
  if (CHART_AVAILABLE() && detail.snapshots.length > 1) {
    const series = detail.snapshots;
    state.charts.media = new window.Chart(el("media-chart").getContext("2d"), {
      type: "line",
      data: {
        labels: series.map((s) => formatDate(s.collected_at, true)),
        datasets: [
          { label: "Curtidas", data: series.map((s) => s.likes), borderColor: "#ee2a7b", tension: 0.3, borderWidth: 2, pointRadius: 2 },
          { label: "Alcance", data: series.map((s) => s.reach), borderColor: "#6228d7", tension: 0.3, borderWidth: 2, pointRadius: 2 },
          { label: "Salvamentos", data: series.map((s) => s.saved), borderColor: "#f9ce34", tension: 0.3, borderWidth: 2, pointRadius: 2 },
        ],
      },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } },
        scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: { color: "#1f2532" } } },
      },
    });
  } else {
    const wrap = el("media-chart");
    if (wrap) {
      wrap.parentElement.innerHTML =
        `<p class="hint">O gráfico de evolução aparece a partir da segunda coleta deste post.</p>`;
    }
  }
}

function closeDialog() {
  el("media-dialog").hidden = true;
  destroyChart("media");
}

document.addEventListener("DOMContentLoaded", boot);
