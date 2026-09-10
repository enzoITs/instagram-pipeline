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

Defina `ADMIN_PASSWORD` no `.env` para exigir login: sem ela o dashboard e a API
ficam abertos a quem alcançar o endereço, e o servidor avisa isso no arranque.

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

Quando a Meta **não devolve** o alcance, a taxa cai para o número de seguidores
como denominador e o campo `engagement_basis` registra qual foi usado. Alcance
**igual a zero** é tratado como medição, não como ausência: se também não houve
interação, a taxa é `0` sobre `reach`; se houve interação com alcance zero — o
que a Meta reporta em posts anteriores à conversão para conta profissional —
nenhum denominador descreve a realidade e o valor fica `null`. Sem nenhum
denominador confiável, o campo também fica `null` — nunca é preenchido com zero,
para não confundir "sem dado" com "engajamento nulo".

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

**Login do dashboard.** `ADMIN_PASSWORD` liga um login de usuário único: a senha
é conferida em tempo constante, a sessão vira um cookie assinado `HttpOnly` com
validade configurável (`SESSION_MAX_AGE_HOURS`) e todas as rotas `/api/*` e
`/auth/accounts/*` passam a exigi-la. `/api/health` continua respondendo sem
sessão, para servir de sonda de uptime, mas só devolve `status` e `database` —
a configuração e a contagem de contas ficam atrás do login. Sem a variável
definida, o comportamento antigo (tudo aberto) permanece, com aviso no arranque.

**Coleta em segundo plano.** Uma execução completa faz uma chamada por post e
pode pausar por minutos quando a Meta sinaliza consumo de cota, então
`POST /collect` grava a execução, devolve **202** com `run_id` e roda fora da
requisição; o dashboard acompanha por `GET /api/runs/{id}`. Uma segunda coleta
para a mesma conta enquanto a primeira roda recebe 409 em vez de duplicar
chamadas. `?wait=true` mantém o comportamento síncrono para contas pequenas.

**Migrações.** O schema é versionado com Alembic e a aplicação roda
`upgrade head` no arranque, então bancos antigos migram sozinhos em vez de
quebrar. Bancos anteriores ao Alembic são carimbados na revisão inicial.

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
├── security.py             login por sessão e proteção das rotas
├── scheduler.py            jobs periódicos (APScheduler), com trava no banco
├── instagram/
│   ├── client.py           cliente da Graph API: erros, retentativas, cota
│   ├── oauth.py            fluxos OAuth (Instagram Login e Facebook Login)
│   └── metrics.py          métricas por formato e cálculo da taxa
├── services/
│   ├── collector.py        o pipeline de coleta
│   ├── jobs_runtime.py     execução das coletas em segundo plano
│   ├── token_service.py    renovação dos tokens de 60 dias
│   └── analytics.py        consultas que alimentam o dashboard
├── routers/                auth, accounts, media, jobs, system
└── static/                 dashboard (HTML, CSS, JS — sem etapa de build)
migrations/                 versões do schema (Alembic)
tests/                      149 testes com a Graph API mockada
scripts/seed_demo.py        dados fictícios para ver o dashboard sem chaves
```

---

## API

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/` | Dashboard |
| `GET` | `/docs` | Documentação interativa (OpenAPI) |
| `GET` | `/api/health` | Estado do serviço (detalhes só com sessão) |
| `GET` | `/auth/session` | Se o login é exigido e se há sessão ativa |
| `POST` | `/auth/session` | Entra no dashboard com a senha |
| `DELETE` | `/auth/session` | Sai do dashboard |
| `GET` | `/auth/login` | Redireciona para a autorização da Meta |
| `GET` | `/auth/login-url` | Mesma autorização, devolvida como JSON |
| `GET` | `/auth/callback` | Alvo do redirecionamento OAuth |
| `DELETE` | `/auth/accounts/{id}` | Desconecta a conta e apaga o token |
| `GET` | `/api/accounts` | Contas conectadas |
| `GET` | `/api/accounts/{id}` | Uma conta |
| `GET` | `/api/accounts/{id}/summary` | Totais consolidados |
| `GET` | `/api/accounts/{id}/timeseries?days=90` | Série temporal por coleta |
| `GET` | `/api/accounts/{id}/breakdown` | Desempenho por formato |
| `GET` | `/api/accounts/{id}/media` | Posts, com filtros, ordenação e paginação |
| `GET` | `/api/accounts/{id}/export.csv` | Exportação completa em CSV |
| `GET` | `/api/media/{id}` | Um post com todo o histórico |
| `POST` | `/api/accounts/{id}/collect` | Dispara uma coleta (202 + `run_id`) |
| `POST` | `/api/collect` | Coleta para todas as contas ativas (202) |
| `POST` | `/api/accounts/{id}/refresh-token` | Renova o token de 60 dias |
| `GET` | `/api/runs` | Histórico de execuções |
| `GET` | `/api/runs/{id}` | Uma execução — é o que o dashboard consulta |

Tudo sob `/api/` e `/auth/accounts/` exige sessão quando `ADMIN_PASSWORD` está
definida; sem sessão a resposta é `401`.

---

## Configuração

Todas as opções ficam no `.env` (documentadas em `.env.example`). As mais usadas:

| Variável | Padrão | Para que serve |
|---|---|---|
| `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` | — | credenciais da Meta (obrigatórias) |
| `SECRET_KEY` | gerada em `data/.secret_key` | assina sessão e `state`, e deriva a chave dos tokens; obrigatória com `ENVIRONMENT=production` |
| `ADMIN_PASSWORD` | — | vazia desliga o login e deixa tudo aberto |
| `SESSION_MAX_AGE_HOURS` | `12` | validade da sessão do dashboard |
| `META_LOGIN_FLOW` | `instagram_login` | `instagram_login` ou `facebook_login` |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | precisa bater com o redirect URI registrado |
| `COLLECTION_INTERVAL_MINUTES` | `360` | frequência da coleta automática |
| `MAX_MEDIA_PER_COLLECTION` | `50` | posts mais recentes por execução |
| `API_CALLS_PER_HOUR` | `180` | teto local de chamadas, compartilhado por todo o processo |
| `BUSINESS_USE_CASE_THRESHOLD` | `80` | % da cota da Meta que faz o coletor pausar |
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

A imagem roda como usuário sem privilégios (uid 10001), traz `HEALTHCHECK` em
`/api/health` e mantém o banco no volume `/app/data`. Uma réplica só: o
agendador vive dentro do processo web e toma uma trava no banco, então réplicas
extras apenas disputariam a trava.

### Migrações do banco

```bash
alembic upgrade head     # aplicado automaticamente no arranque da aplicação
alembic revision --autogenerate -m "descrição"
```

---

## Testes

```bash
.venv/bin/python -m pytest                          # 149 testes
.venv/bin/python -m pytest --cov=app                # com cobertura (95%)
```

Nenhum teste toca a rede: a Graph API é mockada com `respx`, incluindo os
caminhos de erro — token expirado, métrica não suportada, estouro de cota,
falha parcial de coleta e resposta malformada.

---

## Limitações conhecidas

- **Stories não são coletados.** Expiram em 24 horas e não aparecem na aresta
  `/media` que o coletor lê; suportá-los exigiria outra aresta da API e um
  agendamento bem mais frequente.
- **Posts anteriores à conversão** para conta profissional voltam sem alcance e
  sem visualizações — a Meta não tem esses dados.
- **Contas de terceiros** exigem App Review da Meta (veja o SETUP.md).
- **O login é de usuário único** (uma senha, sem contas separadas nem perfis).
  Serve para fechar um dashboard pessoal ou de equipe pequena; para vários
  usuários com permissões distintas, coloque atrás de um proxy autenticado.
- **Sem `ADMIN_PASSWORD` não há autenticação nenhuma** — quem alcança a URL vê
  os dados e pode disparar coletas.
