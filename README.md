# Instagram Pipeline

Duas perguntas sobre o mesmo conteúdo, no mesmo lugar:

- **Como meus posts estão performando ao longo do tempo?** Conecte uma conta
  Business/Creator: o sistema coleta as métricas oficiais (Graph API da Meta) de
  tempos em tempos e guarda **todo o histórico**, que a Meta não mantém.
- **O que estão dizendo nos comentários deste vídeo?** Cole o link de qualquer
  Reel ou post público: o sistema coleta métricas e até 500 comentários,
  classifica **sentimento** (positivo, negativo, neutro, dúvida, spam) e
  **intenção** (quer comprar, hater, elogio, pergunta, outros) com NLP, extrai
  **tópicos** e gera **3–5 insights** acionáveis com IA.

As duas metades se ajudam: quando o link analisado é de um post de uma conta
conectada, a análise usa as métricas **oficiais** (alcance, salvamentos e
compartilhamentos, que o scraping nunca vê) em vez das públicas — e qualquer
análise pode ser **acompanhada**, ganhando o mesmo histórico das contas.

| Camada | Stack |
|---|---|
| Painel | React 18 · TypeScript 5 · Vite 8 · Recharts 2 · Vitest |
| API | Python 3.12 · FastAPI · SQLAlchemy 2 (síncrono + asyncio) · Pydantic 2 |
| Processamento | Celery 5 · Redis 7 · Transformers · Torch (CPU) · scikit-learn · NLTK |
| Dados | PostgreSQL 15 (Alembic) · Redis (cache 24 h + broker + DLQ) |
| Coleta | Graph API da Meta (contas) · Apify / Instaloader (links públicos) |
| IA | OpenAI (insights, com regras como reserva) |
| Deploy | Docker / Podman Compose |

> **Antes de rodar:** o [SETUP.md](SETUP.md) explica o que providenciar — conta
> profissional, app na Meta, URL pública e, se quiser coleta de links públicos,
> as chaves da Apify e da OpenAI.

---

## Início rápido

```bash
cp .env.example .env     # preencha SECRET_KEY, ADMIN_PASSWORD e POSTGRES_PASSWORD
docker compose up -d --build     # ou: podman-compose up -d --build
```

| Serviço | Onde |
|---|---|
| Painel + API | http://localhost:8000 |
| Documentação da API | http://localhost:8000/docs |
| Health check | http://localhost:8000/api/health |

Sem chaves ainda? Rode o seed de demonstração e navegue pelo painel inteiro:

```bash
docker compose exec api python scripts/seed_demo.py
```

Ele cria uma conta fictícia **inativa** (o agendador nunca chama a Meta com o
token falso) com 24 posts e 14 coletas, e duas análises de link com
`SCRAPER_MODE=mock`. Para remover: `scripts/seed_demo.py --reset`.

### Desenvolvimento local

```bash
./run.sh          # sobe postgres+redis, migra, compila o painel, roda API + workers
```

Para trabalhar no frontend com recarga instantânea, deixe o `run.sh` rodando e
abra outro terminal:

```bash
npm --prefix frontend run dev     # http://localhost:5173 (proxy /api, /auth → :8000)
```

---

## Arquitetura

```
  Navegador
     │  login por sessão (cookie assinado, HttpOnly)
     ▼
┌─────────────────────────┐        ┌──────────────┐
│ FastAPI (api)           │───────▶│  PostgreSQL  │◀───────┐
│  /auth  /api  /api/v1   │        └──────────────┘        │
│  + painel React (dist)  │───────▶┌──────────────┐        │
└───────────┬─────────────┘        │    Redis     │        │
            │ publica tarefas      │ cache/broker │        │
            ▼                      └──────┬───────┘        │
   ┌─────────────────┐   fila analysis    │                │
   │ worker (NLP)    │◀───────────────────┤                │
   │ scraping + NLP  │                    │                │
   └─────────────────┘                    │                │
   ┌─────────────────┐   fila collection  │                │
   │ collector (beat)│◀───────────────────┘                │
   │ Graph API, jobs │─────────────────────────────────────┘
   └─────────────────┘
```

**Coleta de uma conta conectada.** O usuário autoriza o app pelo login da Meta
(OAuth 2.0); o token de curta duração é trocado por um de 60 dias, criptografado
com Fernet e guardado no banco. A cada `COLLECTION_INTERVAL_MINUTES` o worker de
coleta lista os posts recentes, busca as métricas de cada um e grava um
**snapshot por post** — é isso que forma o histórico. Uma segunda coleta da mesma
conta enquanto a primeira roda recebe 409 (índice único parcial no banco).

**Análise de um link.** `POST /api/v1/analyze` valida e canoniza a URL, consulta
o cache (Redis, com backup no PostgreSQL) e, se precisar, publica a tarefa na
fila `analysis` → `202`. O worker executa 8 etapas, cada uma em transação curta
que atualiza `videos.current_step`: preparar → coletar → salvar métricas e
comentários → NLP → salvar NLP → calcular engajamento → insights → finalizar e
cachear. O painel consulta `GET /api/v1/analysis/{id}` a cada 2 s e mostra a
etapa atual.

**De onde vêm os números do link.** Se o shortcode do link bate com o
`permalink` de um post de conta conectada e ativa, a etapa de coleta usa a Graph
API (`data_source = graph_api`), com os comentários pela API oficial quando
`ENABLE_GRAPH_COMMENTS=true` ou pelo scraper (`graph_api+apify`). Caso
contrário, é Apify com Instaloader como reserva — e `mock` gera dados
determinísticos de demonstração, sempre sinalizados no painel.

**Confiabilidade.** Retentativas com backoff (5 s, 10 s, 20 s + jitter) para
falhas transitórias; falhas permanentes (post privado) falham na hora; falhas
esgotadas vão para a dead-letter queue `dlq:process_video_analysis`;
`acks_late` + `reject_on_worker_lost` impedem perder um job com o worker morto;
uma análise re-despachada invalida a anterior pelo `task_id`, então duas cópias
nunca gravam a mesma coisa. Jobs periódicos tomam uma trava no banco, e coletas
abandonadas por um worker morto são fechadas por idade a cada 15 minutos.

---

## Fórmula única de engajamento

Os dois lados usam `app/engagement.py`:

```
taxa = (curtidas + comentários + salvamentos + compartilhamentos) ÷ denominador × 100
```

O denominador é a melhor medida de audiência disponível, nesta ordem — e a base
usada é **sempre exibida** junto do número:

| Base | Quando | Origem |
|---|---|---|
| `reach` | alcance conhecido | Graph API (conta conectada) |
| `views` | sem alcance, mas com visualizações | Reels públicos |
| `followers` | o post não expõe nenhum dos dois | perfil da conta |

Alcance igual a zero é tratado como medição, não como ausência: se também não
houve interação, a taxa é `0`; se houve interação com alcance zero — o que a
Meta reporta em posts anteriores à conversão para conta profissional — nenhum
denominador descreve a realidade e o valor fica `null`. Contadores ausentes são
ignorados, nunca contados como zero.

`engagement_score = min(taxa/20 × 10 + proporção_positiva × 3, 10)`, com a
proporção calculada sobre os comentários não-spam.

**Médias por conta são ponderadas**: soma das interações ÷ soma da audiência, e
nunca misturando bases diferentes — a média simples de porcentagens daria a um
post visto por 50 pessoas o mesmo peso de um visto por 50 mil.

---

## API

Documentação interativa em **`/docs`**. Erros seguem o envelope
`{"status": "error", "error": {"code", "message", "error_id?"}, "detail": "…"}`.

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/health` | Estado do serviço (detalhes só com sessão) |
| `GET/POST/DELETE` | `/auth/session` | Estado do login, entrar e sair |
| `GET` | `/auth/login` · `/auth/login-url` | Autorização da Meta |
| `GET` | `/auth/callback` | Alvo do redirecionamento OAuth |
| `DELETE` | `/auth/accounts/{id}` | Desconecta a conta e apaga o token |
| `GET` | `/api/accounts` · `/api/accounts/{id}` | Contas conectadas |
| `GET` | `/api/accounts/{id}/summary` · `/timeseries?days=90` · `/breakdown` | Consolidados |
| `GET` | `/api/accounts/{id}/media` | Posts, com filtros, ordenação e paginação |
| `GET` | `/api/accounts/{id}/export.csv` | Exportação completa em CSV |
| `GET` | `/api/media/{id}` | Um post com seu histórico |
| `POST` | `/api/accounts/{id}/collect` · `/api/collect` | Dispara coleta (202 + `run_id`) |
| `POST` | `/api/accounts/{id}/refresh-token` | Renova o token de 60 dias |
| `GET` | `/api/runs` · `/api/runs/{id}` | Execuções de coleta |
| `POST` | `/api/v1/analyze` | Analisa um link (`202`, ou `200` do cache) |
| `GET` | `/api/v1/analysis/{id}` | Status/resultado da análise |
| `PUT` | `/api/v1/analysis/{id}/tracking` | Acompanha (ou para de acompanhar) o link |
| `DELETE` | `/api/v1/analysis/{id}` | Remove a análise (cascade + cache) |
| `GET` | `/api/v1/recent?limit=10&offset=0` | Análises recentes |

Tudo sob `/api/`, `/api/v1/` e `/auth/accounts/` exige sessão quando
`ADMIN_PASSWORD` está definida — obrigatória com `ENVIRONMENT=production`.
`POST /api/v1/analyze` é limitado a `RATE_LIMIT_PER_MINUTE` por IP e
`POST /auth/session` a `LOGIN_ATTEMPTS_PER_MINUTE`.

```bash
curl -X POST http://localhost:8000/api/v1/analyze \
  -H 'Content-Type: application/json' -b cookies.txt \
  -d '{"url": "https://www.instagram.com/reel/C8xYz12AbCd/"}'
```

---

## Como a análise funciona

- **Coleta pública**: Apify (REST, token no header `Authorization`, orçamento de
  150 s para retentativas) com Instaloader de reserva; contadores ocultos (`-1`)
  viram `null`; comentários deduplicados e truncados em 2200 caracteres.
- **Sentimento**: XLM-RoBERTa multilíngue treinado em redes sociais, mais
  heurísticas de alta precisão para `spam` (links, "sigo de volta", cripto) e
  `dúvida` (perguntas sem polaridade clara).
- **Intenção**: regras por palavras-chave primeiro (compra, insultos,
  perguntas); zero-shot NLI só decide casos ambíguos, e apenas quando compatível
  com o sentimento.
- **Tópicos e palavras-chave**: TF-IDF (unigramas + bigramas) com stopwords
  PT/EN; as frases aparecem marcadas no texto dos comentários do painel.
- **Insights**: OpenAI em JSON mode recebe métricas agregadas, tópicos e 25
  comentários truncados; o prompt trata comentários como dados não confiáveis e
  a saída é validada por schema. Sem chave, resposta inválida ou timeout → regras.

---

## Configuração

Todas as variáveis estão documentadas em [`.env.example`](.env.example). As mais
usadas:

| Variável | Padrão | Para que serve |
|---|---|---|
| `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` | — | credenciais da Meta |
| `SECRET_KEY` | gerada em `data/.secret_key` | assina sessão e `state`; obrigatória em produção |
| `ADMIN_PASSWORD` | — | senha do painel; obrigatória em produção |
| `DATABASE_URL` | PostgreSQL local | um só driver (psycopg 3) serve os dois motores |
| `REDIS_URL` / `CELERY_BROKER_URL` | db 0 / 1 | cache, rate limit, fila |
| `TASK_BACKEND` | `celery` | `inline` roda o job no processo da API (só testes) |
| `COLLECTION_INTERVAL_MINUTES` | `360` | frequência da coleta automática |
| `TRACKING_INTERVAL_MINUTES` | `720` | re-coleta dos links acompanhados (0 desliga) |
| `ENABLE_GRAPH_COMMENTS` | `false` | pede permissão para ler comentários pela API oficial |
| `SCRAPER_MODE` | `auto` | `auto`, `apify`, `instaloader`, `mock` |
| `NLP_BACKEND` | `transformers` | `lexicon` dispensa os modelos |
| `OPENAI_API_KEY` | — | insights com IA (sem ela, regras) |
| `ENABLE_SCHEDULER` | `true` | desliga todos os jobs periódicos |

---

## Testes e qualidade

```bash
# Backend: SQLite + fakeredis por padrão, sem serviços externos
pytest --cov=app
# A mesma suíte contra PostgreSQL real
TEST_DATABASE_URL=postgresql+psycopg://pipeline:pipeline@localhost:5432/pipeline_test pytest
ruff check . && mypy app

# Frontend
npm --prefix frontend test
npm --prefix frontend run lint && npm --prefix frontend run typecheck && npm --prefix frontend run build
```

| Suíte | Resultado |
|---|---|
| Backend (unidade, integração, E2E da API) | 367 testes; o schema é criado pelas migrações |
| Frontend (Vitest + Testing Library) | 63 testes |
| Lint / tipos | ruff, mypy, ESLint, `tsc` sem erros |

APIs externas (Meta, Apify, Instaloader, OpenAI, Hugging Face) são mockadas.

---

## Segurança

- Login de usuário único por sessão: senha conferida em tempo constante, cookie
  assinado `HttpOnly` com validade configurável. Trocar `ADMIN_PASSWORD`
  invalida as sessões já emitidas, e o login tem limite por minuto.
- Tokens da Meta criptografados em repouso (Fernet); renovação automática antes
  do vencimento; token rejeitado marca a conta como inativa com mensagem clara.
- Validação de entrada com Pydantic e canonicalização de URL (host permitido,
  sem credenciais nem porta); SQLAlchemy parametrizado; React renderiza
  comentários como texto, nunca HTML.
- CSP sem scripts de terceiros, `X-Frame-Options`, `nosniff`, `Referrer-Policy`,
  HSTS quando a URL pública é HTTPS; `X-Forwarded-For` só é considerado com
  `TRUST_PROXY_HEADERS=true`.
- Logs em JSON com `request_id` e mascaramento de tokens, chaves e senhas
  (inclusive `access_token` nas URLs da Graph API).
- Prompt injection: comentários são dados não confiáveis no prompt e a saída do
  LLM é validada por schema.
- Exportação CSV neutraliza fórmulas (`=`, `+`, `-`, `@`) nas legendas.

---

## Limitações conhecidas

- **Stories não são coletados**: expiram em 24 h e não aparecem na aresta
  `/media`.
- **Posts anteriores à conversão** para conta profissional voltam sem alcance e
  sem visualizações — a Meta não tem esses dados.
- **Contas de terceiros** exigem App Review da Meta (veja o SETUP.md).
- **Coleta de links de terceiros exige `APIFY_TOKEN`** (ou sessão do
  Instaloader): o Instagram bloqueia acesso anônimo. Links de contas conectadas
  não precisam de nenhum dos dois.
- **O login é de usuário único** (uma senha, sem perfis). Para vários usuários
  com permissões distintas, coloque atrás de um proxy autenticado.
- **Miniaturas expiram**: as URLs de CDN do Instagram deixam de responder depois
  de algumas semanas; o painel simplesmente omite a imagem quebrada.

---

## Estrutura

```
app/
├── main.py             FastAPI, middlewares, handlers, serve o painel
├── config.py           configuração única (Meta, infraestrutura, NLP, IA)
├── database.py         Base, UTCDateTime, motor síncrono + assíncrono, migrações
├── engagement.py       a fórmula (usada pelos dois lados)
├── models.py           Account, Media, MetricSnapshot, CollectionRun, JobLock
├── celery_app.py       filas analysis/collection, beat
├── tasks.py            tarefas Celery
├── scheduler.py        jobs periódicos e a trava no banco
├── security.py         login por sessão
├── instagram/          client (Graph API), oauth, metrics, url
├── routers/            auth, accounts, media, jobs, system, analysis
├── services/           collector, analytics, token_service, jobs_runtime
└── analyzer/           pipeline, graph_source, tracking, models, schemas,
                        repository, services/{scraper,nlp,insights,cache,mock}
frontend/src/
├── App.tsx             shell: login, abas, tema
├── features/accounts/  painel das contas conectadas
├── components/         formulário, painel da análise, gráficos
└── utils/              http, api, accountsApi, router, format, theme…
migrations/             0001–0004 (schema completo)
tests/                  suíte do backend (tests/analyzer/ = análise por link)
scripts/seed_demo.py    dados de demonstração
```
