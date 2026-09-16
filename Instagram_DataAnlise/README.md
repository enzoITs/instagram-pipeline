# Instagram Engagement Analyzer

Analisa o engajamento de Reels e Posts do Instagram: coleta métricas e até 500 comentários,
classifica **sentimento** (positivo, negativo, neutro, dúvida, spam) e **intenção**
(quer comprar, hater, elogio, pergunta, outros) com NLP, extrai **tópicos** e gera **3–5 insights**
acionáveis com IA, tudo exibido em um painel web.

| Camada | Stack |
|---|---|
| Frontend | React 18 · TypeScript 5 · Vite 8 · Recharts 2 · Axios · Vitest |
| API | Python 3.11 · FastAPI · SQLAlchemy 2 (async) · Pydantic 2 |
| Processamento | Celery 5 · Redis 7 · Transformers 5 · Torch 2 (CPU) · scikit-learn · NLTK |
| Dados | PostgreSQL 15 (Alembic) · Redis (cache 24 h + broker + DLQ) |
| Coleta / IA | Apify (REST) · Instaloader (fallback) · OpenAI GPT-4 Turbo (fallback por regras) |
| Deploy | Docker / Podman Compose · nginx |

---

## Sumário

1. [Início rápido (Docker)](#início-rápido-docker)
2. [Arquitetura](#arquitetura)
3. [Configuração](#configuração)
4. [API](#api)
5. [Como a análise funciona](#como-a-análise-funciona)
6. [Desenvolvimento local](#desenvolvimento-local)
7. [Testes e qualidade](#testes-e-qualidade)
8. [Desempenho medido](#desempenho-medido)
9. [Observabilidade e operação](#observabilidade-e-operação)
10. [Segurança](#segurança)
11. [Decisões, desvios da especificação e limitações](#decisões-desvios-da-especificação-e-limitações)
12. [Estrutura do projeto](#estrutura-do-projeto)

---

## Início rápido (Docker)

Pré-requisitos: Docker 24+ com Compose 2.20+ (ou Podman 5 + `podman-compose`), ~6 GB de disco
(imagem do worker + modelos de NLP) e ~4 GB de RAM livres.

```bash
cp .env.example .env
# Edite .env: APIFY_TOKEN (coleta real), OPENAI_API_KEY (insights com IA), POSTGRES_PASSWORD.
docker compose up -d --build
docker compose ps          # aguarde todos "healthy" (o worker baixa ~1,5 GB de modelos na 1ª vez)
```

| Serviço | URL |
|---|---|
| Painel | http://localhost:3000 |
| API + Swagger | http://localhost:8000/docs |
| Health check | http://localhost:8000/health |

Sem credenciais? Use `SCRAPER_MODE=mock` no `.env` para gerar **dados de demonstração** determinísticos
(o painel exibe um aviso claro de que não são dados reais) e deixe `OPENAI_API_KEY` vazio para usar os
insights por regras.

```bash
docker compose logs -f worker                         # logs estruturados do pipeline
docker compose exec postgres psql -U analyzer -c "\dt"  # 7 tabelas + alembic_version
docker compose exec redis redis-cli ping               # PONG
docker compose down            # para os serviços (volumes preservados)
```

---

## Arquitetura

```mermaid
flowchart LR
    U[Navegador] -->|:3000| N[nginx<br/>SPA React]
    N -->|/api, /health| A[FastAPI<br/>:8000]
    A -->|cache 24h / rate limit| R[(Redis)]
    A -->|leitura/escrita async| P[(PostgreSQL)]
    A -->|send_task| R
    R -->|fila analysis| W[Celery worker]
    W --> S[Apify / Instaloader]
    W --> M[Transformers<br/>XLM-R + MiniLM]
    W --> O[OpenAI GPT-4 Turbo]
    W -->|resultados| P
    W -->|SETEX video:id| R
```

**Fluxo de uma análise**

1. `POST /api/v1/analyze` valida e canoniza a URL, consulta o cache (Redis e, se vazio, o backup
   `analysis_cache` no PostgreSQL). Cache hit → `200` com o resultado em milissegundos.
2. Sem cache: cria/reaproveita o registro `videos` (`processing`) e publica a task no Celery → `202`.
3. O worker executa 8 etapas, cada uma em transação curta que atualiza `videos.current_step`:
   preparar → coletar → salvar métricas e comentários → NLP → salvar NLP → calcular engajamento →
   insights → finalizar e gravar cache.
4. O frontend consulta `GET /api/v1/analysis/{id}` a cada 2 s (máx. 60 tentativas = 2 min) e mostra a
   etapa atual; ao concluir, renderiza o painel.

**Confiabilidade**

- Retries com backoff exponencial (5 s, 10 s, 20 s + jitter) para falhas transitórias (Apify/Instagram
  429/5xx, banco indisponível); falhas permanentes (post privado/inexistente) falham na hora.
- Falhas esgotadas ou inesperadas vão para a dead-letter queue `dlq:process_video_analysis` (Redis).
- `acks_late` + `reject_on_worker_lost`: um worker que morre não perde o job.
- Soft/hard time limit de 270/300 s; análises "presas" por mais de 10 min são marcadas como falhas.
- Fallbacks em cada dependência externa: Apify → Instaloader; transformers → léxico; OpenAI → regras;
  Redis fora → API continua (cache vira miss, rate limit libera).
- Pipeline idempotente: uma nova tentativa apaga resultados parciais antes de regravar.

---

## Configuração

Todas as variáveis estão documentadas em [`.env.example`](.env.example). As principais:

| Variável | Padrão | Descrição |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://…` | URL async; worker e Alembic derivam a URL `psycopg2` |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | db 0 / 1 / 2 | cache, broker e resultados em bancos separados |
| `TASK_BACKEND` | `celery` | `inline` roda o pipeline dentro da API (apenas desenvolvimento) |
| `SCRAPER_MODE` | `auto` | `auto` (Apify se houver token, senão Instaloader), `apify`, `instaloader`, `mock` |
| `APIFY_TOKEN` | — | token da Apify (actors `apify~instagram-scraper` e `apify~instagram-comment-scraper`) |
| `INSTAGRAM_USERNAME` / `INSTAGRAM_SESSIONFILE` | — | sessão do Instaloader (necessária para comentários) |
| `NLP_BACKEND` | `transformers` | `lexicon` dispensa modelos (menor precisão) |
| `SENTIMENT_MODEL` | `cardiffnlp/twitter-xlm-roberta-base-sentiment` | sentimento multilíngue treinado em redes sociais |
| `INTENT_MODEL` | `MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli` | zero-shot para intenções ambíguas |
| `NLP_DEVICE` | `auto` | usa GPU automaticamente se o torch com CUDA estiver instalado |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | — / `gpt-4-turbo` | insights com IA (JSON mode) |
| `CACHE_TTL_SECONDS` | `86400` | TTL do cache de análises |
| `RATE_LIMIT_PER_MINUTE` | `10` | limite de `POST /analyze` por IP |
| `CORS_ORIGINS` | `localhost:5173,3000` | origens permitidas (separadas por vírgula) |
| `CELERY_CONCURRENCY` | `2` (compose) | processos do worker; cada um carrega os modelos (~2 GB RAM) |
| `NLP_TORCH_THREADS` | núcleos ÷ processos | threads do torch por processo (evita disputa de CPU entre processos) |
| `LOG_FORMAT` / `LOG_LEVEL` | `json` / `INFO` | logs estruturados em JSON ou texto |

---

## API

Documentação interativa (Swagger) em **`/docs`** e ReDoc em `/redoc`. Todos os erros seguem o envelope:

```json
{ "status": "error", "error": { "code": "invalid_url", "message": "URL inválida: …", "error_id": "só em 500" } }
```

| Método | Rota | Respostas |
|---|---|---|
| `POST` | `/api/v1/analyze` | `202` job criado · `200` cache hit · `400` URL inválida · `429` limite · `503` fila fora |
| `GET` | `/api/v1/analysis/{video_id}` | `200` com `status` = `processing` / `success` / `error` · `404` |
| `GET` | `/api/v1/recent?limit=10&offset=0` | `200` lista paginada (limit ≤ 100) · `400` |
| `DELETE` | `/api/v1/analysis/{video_id}` | `200` removido (cascade + cache) · `404` |
| `GET` | `/health` | `200` ok · `503` degraded (PostgreSQL `SELECT 1` e Redis `PING`, 2 s cada) |
| `GET` | `/docs` | Swagger UI |

```bash
curl -X POST http://localhost:8000/api/v1/analyze \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://www.instagram.com/reel/C8xYz12AbCd/"}'
# {"status":"processing","video_id":"…","message":"Análise iniciada. Resultado disponível em 2-5 minutos."}

curl http://localhost:8000/api/v1/analysis/<video_id>
# {"status":"processing","message":"Análise em andamento... (etapa 4/8: Analisando sentimento, intenção e tópicos)","step":4,"total_steps":8}
# {"status":"success","data":{"video":{…},"metrics":{…},"sentiment":{…},"intents":{…},"top_topics":[…],"insights":[…],"sentiment_comments_sample":[…],"processing":{…}}}
```

URLs aceitas (normalizadas para `https://www.instagram.com/{reel|p}/{id}/`): sem `https://`, `http`,
`instagram.com` ou `m.instagram.com`, `/reels/`, `/{usuario}/reel/{id}/`, com `?igsh=…` ou `#fragmento`.

Campos adicionais em relação ao contrato original (todos aditivos): `intents` (distribuição de intenções),
`processing` (fonte, backend de NLP, origem dos insights, duração por etapa, avisos), `step`/`total_steps`
no status `processing`, `cached` no cache hit, `url` em `/recent` e `cache` (hits/misses) no `/health`.

---

## Como a análise funciona

### Coleta
- **Apify** (REST `run-sync-get-dataset-items`): post e comentários em paralelo, token no header
  `Authorization` (nunca na URL), retries limitados por um orçamento de 150 s.
- **Instaloader** como fallback, com controlador de rate limit que falha rápido em vez de dormir minutos.
- Normalização: deduplicação por id, contadores ocultos (`-1`) viram `null`, timestamps em UTC.
- Compartilhamentos e salvamentos **não são públicos** no Instagram: ficam `null` e fora da taxa.

### NLP
- **Sentimento**: XLM-RoBERTa (Twitter, multilíngue) para positivo/neutro/negativo + heurísticas de alta
  precisão para `spam` (links, "sigo de volta", cripto…) e `dúvida` (perguntas sem polaridade clara).
- **Intenção**: regras por palavras-chave primeiro (compra, insultos, perguntas) e zero-shot NLI apenas
  como segunda opinião para comentários ambíguos, aceita somente quando compatível com o sentimento.
  Benchmark em PT-BR (CPU, 256 comentários): MiniLM-L6 1,3 s; mDeBERTa-v3-base 78 s — mais preciso em
  elogios, mas estouraria o orçamento de 60 s; ambos super-predizem "pergunta", por isso perguntas ficam
  com as regras.
- **Palavras-chave**: TF-IDF (unigramas + bigramas) com stopwords PT/EN; um bigrama absorve seus unigramas.
  Essas frases aparecem **marcadas** no texto dos comentários do painel.
- **Tópicos**: frequência de n-gramas por documento, com deduplicação de tópicos que cobrem os mesmos
  comentários e sentimento médio por tópico (0 = negativo, 1 = positivo).

### Métricas
- `engagement_rate = (likes + comments + shares×2 + saves×1,5) / views × 100` (`null` sem visualizações)
- `engagement_score = min(rate/20×10 + positive_ratio×3, 10)`, com `positive_ratio` sobre comentários não-spam

### Insights
- GPT-4 Turbo em JSON mode recebe métricas agregadas, tópicos e 25 comentários truncados; o prompt trata
  comentários como dados não confiáveis e a saída é validada por schema (tipos, 0–10, 3–5 itens).
- Resposta inválida, parcial, timeout ou ausência de chave → insights por regras (conversão, dúvidas,
  sentimento negativo, spam, tópico dominante, nível de engajamento).

---

## Desenvolvimento local

Pré-requisitos: Python 3.11, Node 18+ (testado com 24), PostgreSQL e Redis (via compose).

```bash
docker compose up -d postgres redis

# Backend
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+asyncpg://analyzer:analyzer@localhost:5432/analyzer
alembic upgrade head
uvicorn app.main:app --reload                      # http://localhost:8000/docs
celery -A app.celery_app worker --loglevel=info    # outro terminal
# Sem worker: TASK_BACKEND=inline uvicorn app.main:app --reload

# Frontend
cd frontend
npm ci
npm run dev                                        # http://localhost:5173 (proxy /api → :8000)
```

---

## Testes e qualidade

```bash
# Backend: SQLite + fakeredis por padrão, sem serviços externos
cd backend && pytest -v --cov=app
# A mesma suíte contra PostgreSQL real
TEST_DATABASE_URL=postgresql+asyncpg://analyzer:analyzer@localhost:5432/analyzer_test pytest
# Teste opcional com os modelos reais (baixa ~1,5 GB)
RUN_MODEL_TESTS=1 pytest tests/test_nlp.py -k real_models
flake8 app tests && mypy app

# Frontend
cd frontend && npm test && npm run test:coverage && npm run lint && npm run typecheck && npm run build
```

| Suíte | Resultado |
|---|---|
| Backend (unit, integração, E2E da API) | 220 testes, **98%** de cobertura; também passa em PostgreSQL 15 |
| Modelos reais (opt-in) | 100 comentários < 10 s com rótulos esperados |
| Frontend (Vitest + Testing Library) | 54 testes, **96%** de cobertura |
| Lint / tipos | flake8, mypy, ESLint, `tsc` e Prettier sem erros |

APIs externas (Apify, Instaloader, OpenAI, Hugging Face) são mockadas nos testes automatizados.

---

## Desempenho medido

Máquina de desenvolvimento: 12 núcleos, sem GPU, modelos em cache.

| Etapa | Meta | Medido |
|---|---|---|
| NLP, 100 comentários (modelos reais) | < 10 s | ~2–4 s |
| Pipeline completo no worker, 238 comentários (coleta simulada) | < 120 s | 1,95 s |
| Formulário → painel no navegador (coleta simulada, via Celery) | < 120 s | 4,5 s |
| Cache hit `POST /analyze` | < 100 ms | 2 ms na API · 80 ms até o painel renderizar |
| Carga dos modelos por processo do worker | — | 3,6 s (1ª execução baixa ~1,5 GB: ~50 s) |
| 1º job em container recém-criado (com warm-up) | — | 2,3 s para 240 comentários (sem warm-up: 14,4 s) |
| Cache hit pelo nginx (stack Docker) | < 100 ms | 3,4 ms |
| Coleta real via Apify | 20–40 s | depende da Apify; limitada a 150 s pelo orçamento de retries |

---

## Observabilidade e operação

- **Logs JSON** com `timestamp`, `level`, `logger`, `module`, `message`, `request_id` e campos extras
  (`video_id`, `step`, `duration_ms`…). Tokens, chaves e senhas de URLs de banco são mascarados.
- **Access log** por requisição com método, rota, status, duração e IP; header `X-Request-ID` propagado.
- **Duração por etapa** gravada em `videos.processing_stats` e exposta em `data.processing`.
- **Erros 500** retornam `error_id` (também no header `X-Error-ID`) para achar o stack trace nos logs.
- **Cache**: hits/misses em `/health` (`cache.hit_ratio`).
- **Celery**: eventos habilitados (`celery -A app.celery_app events` ou Flower); `inspect ping` no healthcheck.
- **Dead-letter queue**: `redis-cli -n 0 LRANGE dlq:process_video_analysis 0 -1`.

---

## Segurança

- Validação de entrada com Pydantic e canonicalização de URL (host allowlist, sem credenciais/porta).
- SQLAlchemy ORM/Core parametrizado (sem SQL concatenado).
- React renderiza comentários como texto; marcações de palavras-chave são nós de texto, nunca HTML.
- CORS restrito a `CORS_ORIGINS`; rate limit por IP com Redis (funciona com várias instâncias).
- `X-Forwarded-For`/`X-Real-IP` só são considerados com `TRUST_PROXY_HEADERS=true` (atrás do nginx).
- nginx com CSP, `X-Frame-Options`, `nosniff` e `Referrer-Policy`; containers rodam como usuário não-root.
- Segredos via variáveis de ambiente; `.env` fora do Git; redaction nos logs.
- Prompt injection: comentários são dados não confiáveis no prompt e a saída do LLM é validada por schema.
- Produção: termine TLS (HTTPS) no load balancer/ingress à frente do nginx.

---

## Decisões, desvios da especificação e limitações

**Desvios deliberados**

| Especificação | Implementado | Motivo |
|---|---|---|
| `psycopg2 (async driver)` | `asyncpg` na API + `psycopg2` no worker/Alembic | psycopg2 não é assíncrono; separar evita misturar contextos sync/async |
| `Apify 1.6+` (SDK) | API REST da Apify via `httpx` | uma chamada síncrona por actor, timeout/retry sob controle, menos dependências |
| Componentes `.jsx` + Tailwind | `.tsx` + CSS com design tokens | a stack pede TypeScript; o checklist pede `global.css`/`Form.css`/`Dashboard.css`/`Loading.css` com variáveis e dark mode |
| Compose com 4 serviços | 5 serviços (worker separado) | o worker Celery precisa de processo e imagem próprios |
| 1 worker Celery por núcleo | padrão do Celery (1 por núcleo), compose usa `CELERY_CONCURRENCY=2` | cada processo carrega ~2 GB de modelos; ajuste conforme a RAM |
| Erros de validação | `400` (e não o `422` padrão do FastAPI) | contrato da especificação; OpenAPI ajustado |
| `.env.example` com chaves reais | placeholders | chaves reais não devem ir para o repositório |

**Limitações conhecidas**

- **Coleta real exige `APIFY_TOKEN`** (ou sessão do Instaloader). O Instagram bloqueia acesso anônimo:
  testado sem token, o Instaloader falhou em 1,4 s com "Fetching Post metadata failed" (tratado como
  falha transitória, com retries e mensagem clara ao usuário).
- O caminho de insights com a OpenAI foi validado com cliente simulado, não contra a API real (sem chave
  neste ambiente); sem chave o sistema usa as regras automaticamente.
- A precisão do zero-shot multilíngue pequeno é limitada; por isso ele só decide casos ambíguos e com
  checagem de compatibilidade com o sentimento.
- Posts de imagem não expõem visualizações: taxa e score ficam `null` e o painel explica o motivo.
- A amostra de comentários do painel é de até 10 por sentimento (máx. 50); o JSON exportado traz a mesma amostra.

---

## Estrutura do projeto

```
.
├── docker-compose.yml          # postgres, redis, backend (API), worker, frontend
├── .env.example
├── backend/
│   ├── Dockerfile              # targets: api, worker
│   ├── alembic/                # migração 0001 (7 tabelas, 5 índices secundários)
│   ├── app/
│   │   ├── main.py             # endpoints + middlewares + OpenAPI
│   │   ├── models.py           # 7 modelos SQLAlchemy
│   │   ├── schemas.py          # contratos Pydantic
│   │   ├── database.py         # engines async (API) e sync (worker)
│   │   ├── repository.py       # consultas compartilhadas + backup do cache
│   │   ├── pipeline.py         # 8 etapas, retries, DLQ
│   │   ├── celery_app.py / tasks.py
│   │   ├── middleware.py       # request id/access log, rate limit
│   │   ├── errors.py / logging_config.py / config.py / instagram_url.py
│   │   └── services/           # scraper, nlp, insights, cache, engagement, mock_data
│   └── tests/                  # 220 testes
└── frontend/
    ├── Dockerfile / nginx.conf
    └── src/
        ├── App.tsx             # estados, polling, histórico
        ├── components/         # AnalysisForm, Dashboard, LoadingSpinner, ErrorBoundary, RecentAnalyses, dashboard/*
        ├── styles/             # global.css, Form.css, Dashboard.css, Loading.css
        └── utils/              # api.ts (HTTP + polling), export.ts, format.ts, text.ts, theme.ts, validation.ts
```
