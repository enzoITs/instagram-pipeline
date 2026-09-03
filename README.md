# Instagram Pipeline

Coleta automatizada, armazenamento histórico e visualização das métricas de
engajamento de contas Instagram Business/Creator, usando a API oficial da Meta.

O sistema substitui a conferência manual de números post a post: um agendador
consulta a Graph API em lotes, grava cada coleta no banco próprio e um dashboard
lê esse histórico. Como a Meta não mantém os dados indefinidamente, o histórico
preservado localmente é o que permite comparar a evolução ao longo do tempo.

> **Antes de rodar:** o [SETUP.md](SETUP.md) explica passo a passo o que você
> precisa providenciar — conta profissional, app na Meta, chaves de API e URL
> pública. Sem isso o sistema sobe, mas não consegue conectar nenhuma conta.

---

## Início rápido

```bash
git clone <este-repositorio> && cd InstagramPipeLine

# 1. Configuração
cp .env.example .env       # preencha as credenciais (veja SETUP.md)

# 2. Subir
./run.sh                   # cria o venv, instala dependências e sobe o servidor
```

Dashboard em http://localhost:8000 · documentação da API em http://localhost:8000/docs

### Ver o dashboard sem ter as chaves ainda

```bash
.venv/bin/python scripts/seed_demo.py    # popula dados fictícios realistas
./run.sh
```

A conta de demonstração nasce inativa, então o agendador nunca tenta chamar a
Meta com o token falso. Para removê-la: `python scripts/seed_demo.py --reset`.

---

## Como funciona

```
  Navegador                    Este sistema                        Meta
 ┌──────────┐   1. login    ┌────────────────┐   OAuth 2.0    ┌──────────────┐
 │          │──────────────▶│  /auth/login   │───────────────▶│  Instagram   │
 │          │◀──────────────│ /auth/callback │◀───────────────│   / Meta     │
 │          │   redireciona └───────┬────────┘  token 60 dias └──────────────┘
 │Dashboard │                       │ (criptografado)                 ▲
 │          │                       ▼                                 │
 │          │               ┌────────────────┐   a cada 6h    ┌───────┴──────┐
 │          │◀──────────────│  Banco (SQLite │◀───────────────│  Coletor     │
 └──────────┘  JSON / CSV   │  ou Postgres)  │   snapshots    │  agendado    │
                            └────────────────┘                └──────────────┘
```

1. O usuário autoriza o app pelo login da Meta (OAuth 2.0).
2. O token de curta duração é trocado por um de **60 dias**, criptografado com
   Fernet e guardado no banco. O sistema nunca vê a senha do usuário.
3. A cada intervalo configurado, o coletor lista os posts recentes e busca as
   métricas de cada um.
4. Cada execução grava um **snapshot** por post — é isso que forma o histórico.
5. O dashboard consulta o banco e desenha gráficos e tabelas.

### Métricas coletadas

| Métrica | Origem | Observação |
|---|---|---|
| Curtidas | campo `like_count` do post | disponível para todo formato |
| Comentários | campo `comments_count` do post | disponível para todo formato |
| Salvamentos | insight `saved` | sinal forte de valor percebido |
| Compartilhamentos | insight `shares` | |
| Alcance | insight `reach` | contas únicas que viram o post |
| Visualizações | insight `views` | substituiu `impressions`/`plays` na v22.0 |
| Interações totais | insight `total_interactions` | somado localmente se a Meta não retornar |
| **Taxa de engajamento** | calculada | `(curtidas + comentários + salvamentos + compartilhamentos) ÷ alcance × 100` |

Quando o alcance não está disponível, a taxa cai para o número de seguidores
como denominador e o campo `engagement_basis` registra qual foi usado. Se nem um
nem outro existir, o valor fica `null` — nunca é preenchido com zero, para não
confundir "sem dado" com "engajamento nulo".

---

## Decisões de projeto

**Métricas depreciadas.** A Meta removeu `impressions`, `plays`, `video_views` e
todas as `carousel_album_*` na Graph API v22.0 (21/04/2025), substituindo-as por
`views`. O sistema pede apenas métricas ainda suportadas e, se a Meta recusar
alguma para um formato específico, remove-a e refaz a chamada automaticamente.

**Limite de requisições.** Há um limitador local de janela deslizante
(`API_CALLS_PER_HOUR`) e leitura do cabeçalho `X-Business-Use-Case-Usage`: se a
Meta informar que boa parte da cota já foi gasta, o coletor pausa pelo tempo que
ela própria estima. Erros 429 e códigos 4/17/32/613/80004 acionam retentativa
com espera exponencial.

**Histórico permanente.** Cada execução insere uma nova linha em
`metric_snapshots` em vez de sobrescrever a anterior. Todos os snapshots de uma
mesma execução compartilham o mesmo `collected_at`, o que alinha os pontos no
gráfico e torna a execução idempotente pelo índice único.

**Tokens.** Guardados criptografados (Fernet). Um job renova os tokens de 60 dias
quando faltam 10 para o vencimento. Token rejeitado marca a conta como inativa
com uma mensagem explicando que basta reconectar — em vez de falhar em silêncio.

**Fuso horário.** Todo timestamp é gravado e devolvido em UTC explícito. O SQLite
não guarda o fuso, então um tipo de coluna dedicado reanexa UTC na leitura; sem
isso o navegador interpretaria os horários como locais e mostraria as coletas
com várias horas de diferença.

---

## Estrutura

```
app/
├── main.py                 aplicação FastAPI, ciclo de vida, tratadores de erro
├── config.py               configurações via ambiente / .env
├── database.py             engine, sessão, tipo UTCDateTime
├── models.py               Account, Media, MetricSnapshot, CollectionRun
├── schemas.py              modelos de resposta (Pydantic)
├── crypto.py               criptografia dos tokens em repouso
├── scheduler.py            jobs periódicos (APScheduler)
├── instagram/
│   ├── client.py           cliente da Graph API: erros, retentativas, cota
│   ├── oauth.py            fluxos OAuth (Instagram Login e Facebook Login)
│   └── metrics.py          métricas por formato e cálculo da taxa
├── services/
│   ├── collector.py        o pipeline de coleta
│   ├── token_service.py    renovação dos tokens de 60 dias
│   └── analytics.py        consultas que alimentam o dashboard
├── routers/                auth, accounts, media, jobs, system
└── static/                 dashboard (HTML, CSS, JS — sem etapa de build)
tests/                      102 testes com a Graph API mockada
scripts/seed_demo.py        dados fictícios para ver o dashboard sem chaves
```

---

## API

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/` | Dashboard |
| `GET` | `/docs` | Documentação interativa (OpenAPI) |
| `GET` | `/api/health` | Estado do serviço e da configuração |
| `GET` | `/auth/login` | Redireciona para a autorização da Meta |
| `GET` | `/auth/callback` | Alvo do redirecionamento OAuth |
| `DELETE` | `/auth/accounts/{id}` | Desconecta a conta e apaga o token |
| `GET` | `/api/accounts` | Contas conectadas |
| `GET` | `/api/accounts/{id}/summary` | Totais consolidados |
| `GET` | `/api/accounts/{id}/timeseries?days=90` | Série temporal por coleta |
| `GET` | `/api/accounts/{id}/breakdown` | Desempenho por formato |
| `GET` | `/api/accounts/{id}/media` | Posts, com filtros, ordenação e paginação |
| `GET` | `/api/accounts/{id}/export.csv` | Exportação completa em CSV |
| `GET` | `/api/media/{id}` | Um post com todo o histórico |
| `POST` | `/api/accounts/{id}/collect` | Dispara uma coleta imediata |
| `POST` | `/api/collect` | Coleta para todas as contas ativas |
| `POST` | `/api/accounts/{id}/refresh-token` | Renova o token de 60 dias |
| `GET` | `/api/runs` | Histórico de execuções |

---

## Configuração

Todas as opções ficam no `.env` (documentadas em `.env.example`). As mais usadas:

| Variável | Padrão | Para que serve |
|---|---|---|
| `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` | — | credenciais da Meta (obrigatórias) |
| `META_LOGIN_FLOW` | `instagram_login` | `instagram_login` ou `facebook_login` |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | precisa bater com o redirect URI registrado |
| `COLLECTION_INTERVAL_MINUTES` | `360` | frequência da coleta automática |
| `MAX_MEDIA_PER_COLLECTION` | `50` | posts mais recentes por execução |
| `API_CALLS_PER_HOUR` | `180` | teto local de chamadas |
| `DATABASE_URL` | SQLite local | aceita PostgreSQL |
| `ENABLE_SCHEDULER` | `true` | `false` desliga os jobs |

### PostgreSQL

```bash
pip install "psycopg[binary]"
```
```env
DATABASE_URL=postgresql+psycopg://usuario:senha@localhost:5432/instagram_pipeline
```

### Docker

```bash
cp .env.example .env    # preencha
docker compose up --build
```

---

## Testes

```bash
.venv/bin/python -m pytest                          # 102 testes
.venv/bin/python -m pytest --cov=app                # com cobertura (93%)
```

Nenhum teste toca a rede: a Graph API é mockada com `respx`, incluindo os
caminhos de erro — token expirado, métrica não suportada, estouro de cota,
falha parcial de coleta e resposta malformada.

---

## Limitações conhecidas

- **Stories** expiram em 24 horas e não aparecem em `/media`; o suporte a
  métricas de stories existe no código, mas exige coletar dentro dessa janela.
- **Posts anteriores à conversão** para conta profissional voltam sem alcance e
  sem visualizações — a Meta não tem esses dados.
- **Contas de terceiros** exigem App Review da Meta (veja o SETUP.md).
- **Não há autenticação de usuário no dashboard.** Quem alcança a URL vê os
  dados e pode disparar coletas. Para expor publicamente, coloque atrás de um
  proxy autenticado ou de uma VPN.
