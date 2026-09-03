# Setup — o que você precisa fazer para o projeto ficar 100%

Este documento lista tudo o que **só você pode fazer**: criar o app na Meta,
obter as chaves e configurar o ambiente. O código já está pronto e testado; sem
estas credenciais ele roda, mas não consegue falar com o Instagram.

Tempo estimado: 30 a 45 minutos (a maior parte é esperar telas da Meta).

---

## Resumo do que é necessário

| # | Item | Obrigatório? | Onde se obtém |
|---|------|--------------|---------------|
| 1 | Conta Instagram **Business** ou **Creator** | Sim | App do Instagram → Configurações |
| 2 | Conta de desenvolvedor na Meta | Sim | https://developers.facebook.com |
| 3 | **App ID** e **App Secret** | Sim | Painel do app na Meta |
| 4 | URL pública HTTPS (redirect URI) | Sim | ngrok / Cloudflare Tunnel / seu domínio |
| 5 | `SECRET_KEY` e `TOKEN_ENCRYPTION_KEY` | Sim (produção) | Gerados por comando local |
| 6 | Revisão do app (*App Review*) | Só para contas de terceiros | Painel do app na Meta |

Nenhum destes itens tem custo. A Graph API do Instagram é gratuita dentro dos
limites de requisição.

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
```

> Se `TOKEN_ENCRYPTION_KEY` ficar vazio, uma chave é derivada de `SECRET_KEY` —
> aceitável para desenvolvimento. **Trocar `SECRET_KEY` depois invalida todos os
> tokens já salvos**, e as contas precisarão ser reconectadas.

---

## Passo 6 — Rodar

```bash
cp .env.example .env     # e preencha os valores dos passos acima
./run.sh                 # cria o venv, instala tudo e sobe o servidor
```

Abra a URL pública do túnel (não `localhost`) e clique em
**Conectar Instagram**. Depois de autorizar, clique em **Coletar agora**.

---

## Passo 7 — Revisão da Meta (*App Review*): quando é necessária

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
- [ ] `./run.sh` sobe sem erros e `/api/health` responde `"meta_configured": true`
- [ ] Botão "Coletar agora" traz posts reais

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
