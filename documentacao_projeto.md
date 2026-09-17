# Instagram Pipeline — Documentação do Projeto

## 1. Objetivo e problema resolvido

Duas perguntas que antes exigiam trabalho manual — ou dois sistemas separados —
são respondidas aqui pelo mesmo produto:

1. **"Como meus posts estão indo ao longo do tempo?"** Saber isso pelo aplicativo
   exige abrir post por post e anotar números que a Meta não guarda para sempre.
2. **"O que as pessoas estão dizendo nos comentários deste vídeo?"** Ler
   centenas de comentários à mão é inviável, e o Instagram não classifica nada.

O sistema automatiza as duas coisas: coleta as métricas oficiais das contas
conectadas em intervalos programados e guarda o histórico completo; e analisa
qualquer Reel ou post público a partir do link, classificando os comentários por
sentimento e intenção, extraindo tópicos e gerando recomendações.

## 2. Como o sistema funciona

- **Tipo de conta**: o Instagram só libera métricas como alcance e salvamentos
  para contas **Business** ou **Creator**, e apenas para o dono do post.
- **Autenticação (OAuth 2.0)**: o usuário autoriza o app uma vez; a Meta devolve
  um token que o sistema troca por um de 60 dias, criptografa (Fernet) e guarda.
  A senha do usuário nunca passa pelo sistema.
- **Coleta agendada**: a cada intervalo configurado, um worker lista os posts
  recentes da conta e busca as métricas de cada um, gravando **um snapshot por
  post por execução** — é isso que forma o histórico.
- **Análise por link**: a URL é validada e canonizada; um worker coleta métricas
  e comentários, roda o NLP, calcula o engajamento e gera insights. O resultado
  fica em cache por 24 horas.
- **A ponte entre as duas metades**: se o link analisado for de um post de uma
  conta conectada, as métricas vêm da API oficial — incluindo alcance,
  salvamentos e compartilhamentos, que o scraping público nunca enxerga.

## 3. Fluxo de dados

**Conta conectada**
1. O usuário conecta a conta pelo login da Meta e autoriza o acesso.
2. O token de curta duração é trocado por um de 60 dias e guardado criptografado.
3. Em intervalos programados, o worker lista os posts recentes.
4. Para cada post, busca as métricas de engajamento.
5. Os dados são processados (incluindo a taxa de engajamento) e gravados como um
   novo snapshot.
6. O painel lê o banco e desenha gráficos, tabelas e o histórico por post.

**Link público**
1. O usuário cola a URL; a API responde na hora com um identificador de trabalho.
2. O worker coleta o post (API oficial, Apify ou Instaloader) e até 500 comentários.
3. O NLP classifica sentimento e intenção, extrai palavras-chave e tópicos.
4. A taxa e o score de engajamento são calculados com a fórmula única do projeto.
5. A IA (ou as regras, sem chave) gera de 3 a 5 insights.
6. O painel mostra o resultado; se o link for "acompanhado", o agendador volta a
   coletar só os números periodicamente, formando um histórico também para ele.

## 4. Principais métricas

| Métrica | O que significa | Disponível em |
|---|---|---|
| Curtidas | Quantas pessoas curtiram | Sempre |
| Comentários | Volume de interação por texto | Sempre |
| Visualizações | Quantas vezes o vídeo foi assistido | Reels (públicos e próprios) |
| Alcance | Contas únicas que viram o post | Só para o dono do post |
| Salvamentos | Quem guardou para ver depois — sinal forte de valor | Só para o dono |
| Compartilhamentos | Quantas vezes foi enviado a outras pessoas | Só para o dono |
| Taxa de engajamento | Interações ÷ audiência × 100 | Sempre que houver denominador |
| Score de engajamento | 0 a 10, combinando a taxa e o tom dos comentários | Análises por link |
| Sentimento / intenção | Distribuição dos comentários em 5 categorias cada | Análises por link |

A taxa usa sempre a melhor audiência disponível — alcance, depois visualizações,
depois seguidores — e a base escolhida é exibida junto do número, para que
ninguém compare percentuais medidos sobre coisas diferentes.

## 5. Limitações técnicas e como o projeto lida com elas

- **Limite de requisições da Meta**: há um limitador local por hora e a leitura
  do cabeçalho de consumo da própria Meta; quando a cota aperta, o coletor pausa
  pelo tempo que ela estima. O worker de coleta roda com um processo só, porque
  o limite é contado por processo.
- **Retenção de dados da Meta**: as métricas antigas somem do lado da Meta, por
  isso cada coleta grava uma linha nova no banco em vez de sobrescrever.
- **Métricas depreciadas**: `impressions`, `plays` e `video_views` saíram na
  v22.0 da API; o sistema pede só o que ainda existe e, se a Meta recusar uma
  métrica para um formato, remove-a e refaz a chamada.
- **Instagram bloqueia acesso anônimo**: analisar links de terceiros exige uma
  chave da Apify (ou uma sessão do Instaloader). Links de contas conectadas não
  precisam de nenhum dos dois.
- **Falhas acontecem**: retentativas com espera exponencial para erros
  temporários, fila de mensagens mortas para os casos perdidos, travas no banco
  para não coletar a mesma conta duas vezes e varredura de execuções abandonadas
  por um worker que morreu.
- **Custo e privacidade da IA**: sem chave da OpenAI o sistema gera insights por
  regras; os comentários entram no prompt como dados não confiáveis e a resposta
  do modelo é validada por schema antes de ser usada.

## 6. Segurança em uma frase

O painel inteiro fica atrás de uma senha (obrigatória em produção), os tokens da
Meta são criptografados em repouso, o login tem limite de tentativas por minuto,
trocar a senha derruba as sessões antigas e os logs mascaram qualquer token,
chave ou senha antes de serem gravados.

## 7. Onde ler mais

- [README.md](README.md) — arquitetura, API, fórmula de engajamento e testes.
- [SETUP.md](SETUP.md) — passo a passo das credenciais e do primeiro acesso.
- `/docs` na aplicação em execução — documentação interativa da API.
