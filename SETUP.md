# Setup — o que você precisa fazer para o projeto ficar 100%

Este documento lista tudo o que **só você pode fazer**: criar o app na Meta,
obter as chaves e configurar o ambiente. O código já está pronto e testado; sem
estas credenciais ele roda, mas não consegue falar com o Instagram.

Tempo estimado: 30 a 45 minutos (a maior parte é esperar telas da Meta).

O projeto tem duas metades: **contas conectadas** (métricas oficiais e histórico,
exige os passos 1 a 5) e **análise por link** (qualquer post público, exige as
chaves opcionais do passo 6).

---

## Resumo do que é necessário

| # | Item | Obrigatório? | Onde se obtém |
|---|------|--------------|---------------|
| 1 | Conta Instagram **Business** ou **Creator** | Sim | App do Instagram → Configurações |
| 2 | Conta de desenvolvedor na Meta | Sim | https://developers.facebook.com |
| 3 | **App ID** e **App Secret** | Sim | Painel do app na Meta |
| 4 | URL pública HTTPS (redirect URI) | Sim | ngrok / Cloudflare Tunnel / seu domínio |
| 5 | `SECRET_KEY` e `TOKEN_ENCRYPTION_KEY` | Sim (produção) | Gerados por comando local |
| 6 | `ADMIN_PASSWORD` (senha do dashboard) | Sim, se o endereço não for só seu | Escolhida por você |
| 7 | Revisão do app (*App Review*) | Só para contas de terceiros | Painel do app na Meta |
| 8 | PostgreSQL 15 e Redis 7 | Sim | Sobem pelo `docker compose` / `podman-compose` |
| 9 | `APIFY_TOKEN` | Só para analisar links de **terceiros** | https://console.apify.com → Settings → Integrations |
| 10 | `OPENAI_API_KEY` | Não (sem ela, insights por regras) | https://platform.openai.com/api-keys |

A Graph API do Instagram é gratuita dentro dos limites de requisição, e
PostgreSQL e Redis sobem em contêineres locais. Apify e OpenAI são pagos por
uso e **opcionais**: sem eles, o sistema analisa normalmente os posts das contas
conectadas (pela API oficial) e gera insights por regras.

---

## Passo 1 — Converter a conta do Instagram para Business ou Creator

A Insights API **não existe para contas pessoais**. Sem este passo nada
funciona, independentemente das chaves.

1. No app do Instagram, vá em **Configurações e privacidade → Tipo de conta e ferramentas**.
2. Escolha **Mudar para conta profissional**.
3. Selecione **Criador de conteúdo** ou **Empresa**. Qualquer uma das duas serve.

> Métricas só existem a partir do momento da conversão. Posts publicados antes
> de a conta virar profissional podem voltar sem alcance e sem visualizações.

---

## Passo 2 — Criar o app na Meta

1. Acesse https://developers.facebook.com e faça login.
2. Se for a primeira vez, aceite os termos de desenvolvedor e confirme o e-mail.
3. Vá em **Meus apps → Criar app**.
4. Em "Casos de uso", escolha **Outro** e depois o tipo **Empresa** (*Business*).
5. Dê um nome ao app (por exemplo, `Instagram Pipeline`) e crie.

---

## Passo 3 — Adicionar o produto Instagram e pegar as chaves

O projeto suporta dois fluxos de login. **Escolha um.**

### Opção A — `instagram_login` (recomendada, é o padrão)

O usuário entra com a própria conta do Instagram. Não exige Página do Facebook.

1. No painel do app, adicione o produto **Instagram**.
2. Abra **Instagram → Configuração da API com login do Instagram**
   (*API setup with Instagram business login*).
3. Anote o **Instagram App ID** e o **Instagram App Secret** que aparecem ali.
4. Em **Configurar login de negócios → URI de redirecionamento OAuth válidos**,
   adicione exatamente:
   ```
   https://SEU-DOMINIO/auth/callback
   ```
5. Em **Permissões**, confirme que estão pedidas:
   - `instagram_business_basic`
   - `instagram_business_manage_insights`
6. Em **Testadores do Instagram**, adicione a sua conta do Instagram e aceite o
   convite em https://www.instagram.com/accounts/manage_access/ (aba
   *Convites de testador*). Sem isso, a Meta bloqueia o login enquanto o app
   estiver em modo de desenvolvimento.

No `.env`:
```env
META_LOGIN_FLOW=instagram_login
INSTAGRAM_APP_ID=<Instagram App ID>
INSTAGRAM_APP_SECRET=<Instagram App Secret>
```

### Opção B — `facebook_login`

O usuário entra com o Facebook e o sistema lê a conta do Instagram vinculada a
uma Página. Necessária se você também quiser dados da Página, ou se for atender
clientes que já operam por Business Manager.

Requisitos extras: uma **Página do Facebook** com a conta do Instagram
**vinculada** (Meta Business Suite → Configurações → Contas do Instagram).

1. No painel do app, adicione o produto **Login do Facebook**.
2. Em **Login do Facebook → Configurações → URIs de redirecionamento OAuth válidos**,
   adicione `https://SEU-DOMINIO/auth/callback`.
3. Pegue **App ID** e **Chave Secreta do App** em **Configurações do app → Básico**.
4. Permissões necessárias: `instagram_basic`, `instagram_manage_insights`,
   `pages_show_list`, `pages_read_engagement`, `business_management`.

No `.env`:
```env
META_LOGIN_FLOW=facebook_login
INSTAGRAM_APP_ID=<App ID>
INSTAGRAM_APP_SECRET=<Chave Secreta do App>
```

---

## Passo 4 — Ter uma URL pública HTTPS

A Meta **exige HTTPS** no URI de redirecionamento e não aceita `http://localhost`
no fluxo do Instagram. Para desenvolver na sua máquina, use um túnel.

**ngrok** (mais simples):
```bash
# instale em https://ngrok.com/download e autentique uma vez
ngrok http 8000
```
Ele imprime algo como `https://a1b2c3d4.ngrok-free.app`. Use essa URL.

**Cloudflare Tunnel** (alternativa sem cadastro):
```bash
cloudflared tunnel --url http://localhost:8000
```

Depois, no `.env`:
```env
PUBLIC_BASE_URL=https://a1b2c3d4.ngrok-free.app
```

E registre no painel da Meta, **exatamente igual**, com o caminho no fim:
```
https://a1b2c3d4.ngrok-free.app/auth/callback
```

> A URL gratuita do ngrok muda a cada reinício. Toda vez que mudar, atualize o
> `.env` **e** a lista de URIs no painel da Meta.
>
> Em produção, use seu próprio domínio com certificado TLS
> (`PUBLIC_BASE_URL=https://metricas.suaempresa.com`).

---

## Passo 5 — Gerar as chaves de segurança locais

Estas não vêm da Meta; são geradas por você:

```bash
# Assina o parâmetro `state` do OAuth (proteção contra CSRF)
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Criptografa os tokens de acesso salvos no banco
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

No `.env`:
```env
SECRET_KEY=<primeiro valor>
TOKEN_ENCRYPTION_KEY=<segundo valor>
ADMIN_PASSWORD=<a senha que você vai usar para entrar no dashboard>
```

> Se `TOKEN_ENCRYPTION_KEY` ficar vazio, uma chave é derivada de `SECRET_KEY` —
> aceitável para desenvolvimento. **Trocar `SECRET_KEY` depois invalida todos os
> tokens já salvos**, e as contas precisarão ser reconectadas.

> Em desenvolvimento você pode deixar `SECRET_KEY` comentada: o app gera uma
> chave na primeira execução e a guarda em `data/.secret_key`, então reiniciar
> não derruba os tokens salvos. Deixar a linha presente e **vazia** é diferente
> disso e não é aceito. Com `ENVIRONMENT=production` o app se recusa a subir
> sem uma chave de verdade.

> `ADMIN_PASSWORD` vazia significa **nenhuma autenticação**: qualquer um que
> alcance a URL vê os dados, dispara coletas (gastando sua cota da Meta) e pode
> desconectar contas. Como o túnel do Passo 4 é um endereço público, preencha.

---

## Passo 6 — Chaves opcionais da análise por link

A análise de um post **de uma conta conectada** usa a API oficial da Meta e não
precisa de mais nada. Para analisar links de **outras** pessoas, o Instagram
bloqueia acesso anônimo, então é preciso uma das opções abaixo:

```env
# Apify: coleta gerenciada (paga por uso), o caminho recomendado
APIFY_TOKEN=apify_api_...

# ou uma sessão do Instaloader, se você já tiver o arquivo de sessão
INSTAGRAM_USERNAME=seu.usuario
INSTAGRAM_SESSIONFILE=/caminho/para/session-seu.usuario

# ou, para ver o painel com dados fictícios determinísticos:
SCRAPER_MODE=mock
```

Os insights com IA são opcionais: com `OPENAI_API_KEY` o texto vem do modelo,
sem ela vem de regras que usam as mesmas métricas.

Para ler os comentários de posts próprios pela API oficial (em vez de coletá-los),
ative `ENABLE_GRAPH_COMMENTS=true` e peça a permissão
`instagram_business_manage_comments` (ou `instagram_manage_comments`, no fluxo B)
no painel da Meta. Quem já conectou a conta precisa reconectá-la para conceder a
permissão nova.

---

## Passo 7 — Banco, fila e como subir

O sistema usa PostgreSQL (histórico), Redis (cache, limite de requisições e
fila) e dois workers Celery: um para as análises (carrega os modelos de NLP) e
outro para a coleta da Meta e os jobs periódicos.

**Tudo em contêineres** (jeito mais simples):
```bash
cp .env.example .env     # preencha os valores dos passos acima
docker compose up -d --build     # ou: podman-compose up -d --build
docker compose ps                # aguarde "healthy"; o worker baixa ~1,5 GB de modelos na 1ª vez
```

**Na sua máquina** (o script sobe só o banco e o Redis em contêiner):
```bash
./run.sh     # cria o venv, migra, compila o painel e roda API + os dois workers
```

Abra a URL pública do túnel (não `localhost`). O painel pede a senha do
`ADMIN_PASSWORD`, abre na aba **Minhas contas**, e ali você clica em **Conectar
conta do Instagram**. Terminada a autorização, use **Coletar agora** — a coleta
roda em segundo plano e a tela acompanha o progresso. Na aba **Análise por
link** você cola qualquer URL de Reel ou post.

Sem chaves ainda? `python scripts/seed_demo.py` popula uma conta fictícia
(inativa, para o agendador nunca chamar a Meta) e duas análises de exemplo.

**Vindo da versão com SQLite?** O histórico em `data/instagram_pipeline.db` não
vai sozinho para o PostgreSQL. Com o PostgreSQL novo no ar (e ainda vazio):
```bash
python scripts/import_sqlite.py data/instagram_pipeline.db
```
O arquivo original não é alterado, e o script se recusa a importar num banco que
já tenha dados. Para os tokens continuarem legíveis, o `.env` novo precisa da
mesma chave da instalação antiga — se você nunca definiu `SECRET_KEY`, use o
conteúdo de `data/.secret_key`. O script avisa quais contas ficaram com token
ilegível (essas precisam ser reconectadas).

---

## Passo 8 — Revisão da Meta (*App Review*): quando é necessária

| Cenário | Precisa de App Review? |
|---------|------------------------|
| Você conecta apenas a **sua própria** conta (e as de testadores adicionados) | **Não** |
| Terceiros — clientes de agência, outros criadores — vão conectar as contas deles | **Sim** |

Enquanto o app está em **modo de desenvolvimento**, apenas contas listadas como
administradores, desenvolvedores ou testadores do app conseguem autorizar. Isso
cobre o uso pessoal e o piloto interno.

Para liberar o acesso a terceiros, envie o app para revisão pedindo
`instagram_business_basic` e `instagram_business_manage_insights` (ou, no fluxo
B, `instagram_basic` e `instagram_manage_insights`). A Meta pede:

- um vídeo de tela mostrando o fluxo completo de login e o uso dos dados;
- uma política de privacidade pública em URL própria;
- uma explicação em texto de por que cada permissão é necessária;
- verificação de negócio (*Business Verification*) para o fluxo de Página.

O prazo costuma ser de alguns dias úteis.

---

## Checklist final

- [ ] Conta do Instagram é Business ou Creator
- [ ] App criado na Meta, com o produto Instagram (ou Login do Facebook) adicionado
- [ ] `INSTAGRAM_APP_ID` e `INSTAGRAM_APP_SECRET` no `.env`
- [ ] `META_LOGIN_FLOW` corresponde ao fluxo configurado no painel
- [ ] URL HTTPS pública ativa e igual em `PUBLIC_BASE_URL` e no painel da Meta
- [ ] `<PUBLIC_BASE_URL>/auth/callback` registrado como URI de redirecionamento
- [ ] Sua conta aceita como testadora do app
- [ ] `SECRET_KEY` e `TOKEN_ENCRYPTION_KEY` preenchidos
- [ ] `ADMIN_PASSWORD` definida (o endereço do túnel é público)
- [ ] `./run.sh` (ou `docker compose up`) sobe sem erros
- [ ] `/api/health` responde `"meta_configured": true`, `"database": "ok"` e `"redis": "ok"`
- [ ] Botão "Coletar agora" traz posts reais
- [ ] Analisar o link de um post seu mostra o selo "Métricas oficiais da conta conectada"

---

## Problemas comuns

**`Invalid platform app` ou `Invalid client_id`**
Você está usando o App ID errado. No fluxo A, use o **Instagram App ID** da tela
"API setup with Instagram business login", que é diferente do App ID geral.

**`redirect_uri` não corresponde / `URL Blocked`**
A URL registrada na Meta precisa ser idêntica caractere por caractere, incluindo
`https://`, o caminho `/auth/callback` e a ausência de barra no final.

**Login abre e volta com "erro de permissão"**
Sua conta não está na lista de testadores do app, ou o convite não foi aceito em
https://www.instagram.com/accounts/manage_access/.

**Coleta funciona, mas alcance e visualizações vêm vazios**
Normal para posts publicados antes de a conta virar profissional, e para posts
com menos de 24 horas em alguns formatos. Curtidas e comentários continuam
vindo, porque são lidos do próprio post e não da Insights API.

**`(#100) ... metric ... not supported`**
Já é tratado: o sistema remove a métrica não suportada e refaz a chamada. Se
aparecer no log, é apenas informativo.

**Conta aparece como "desconectada" sozinha**
O token de 60 dias expirou ou foi revogado (troca de senha, remoção do app nas
configurações do Instagram). Basta reconectar pelo dashboard. O agendador tenta
renovar automaticamente 10 dias antes do vencimento.
