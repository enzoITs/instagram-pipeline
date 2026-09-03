# [Nome do Projeto] — Documentação do Projeto

## 1. Objetivo e Problema Resolvido

Hoje, saber se um vídeo ou Reel "performou bem" no Instagram exige entrar no app, abrir cada post individualmente e anotar os números manualmente — um processo lento, sujeito a erro e sem histórico organizado ao longo do tempo.

O **[Nome do Projeto]** resolve isso automatizando a coleta de métricas de engajamento (curtidas, comentários, salvamentos, alcance, impressões e visualizações) diretamente da API oficial da Meta. O sistema guarda um histórico próprio desses dados e os apresenta em um dashboard, permitindo acompanhar a evolução do engajamento sem trabalho manual e comparar posts entre si ao longo do tempo.

## 2. Como o Sistema Funciona

- **Tipo de conta exigida**: o Instagram só libera esses dados para contas **Business** ou **Creator** — contas pessoais não têm acesso a essas informações pela API.
- **Autenticação (OAuth 2.0)**: o usuário faz login uma única vez e autoriza o sistema a acessar os dados da sua conta. Em troca, a Meta devolve um **token de acesso** — uma espécie de chave digital temporária que o sistema usa para se identificar em cada requisição, sem precisar da senha do usuário.
- **Endpoints consultados**: o sistema usa a *Instagram Graph API (Insights API)* para (a) listar os posts recentes da conta e (b) buscar as métricas de cada post individualmente.
- **Armazenamento e exibição**: os dados retornados pela Meta são salvos em um banco de dados próprio do sistema, e não apenas exibidos "ao vivo" — isso garante um histórico permanente, já que a Meta não guarda esses dados indefinidamente. O dashboard lê esse banco e exibe gráficos e tabelas com a evolução do engajamento.

## 3. Fluxo de Dados: Do Login ao Dashboard

1. O usuário conecta sua conta Instagram Business/Creator via login da Meta e autoriza o acesso.
2. O sistema recebe um token de acesso de curta duração e o troca por um token de longa duração (válido por 60 dias).
3. Em intervalos programados, o sistema consulta a API para listar os posts recentes da conta.
4. Para cada post, o sistema busca as métricas de engajamento correspondentes.
5. Os dados são processados (incluindo o cálculo da taxa de engajamento) e armazenados no banco próprio.
6. O dashboard consulta esse banco e apresenta os números em gráficos e tabelas atualizados.

## 4. Principais Métricas Coletadas

| Métrica | O que significa |
|---|---|
| Curtidas | Quantas pessoas curtiram o post |
| Comentários | Volume de interação por texto |
| Salvamentos | Quantas pessoas guardaram o post para ver depois — forte sinal de valor percebido |
| Compartilhamentos | Quantas vezes o post foi enviado a outras pessoas |
| Alcance | Número de contas únicas que viram o post |
| Visualizações | Quantas vezes o vídeo foi assistido |
| Taxa de engajamento | Métrica calculada: soma das interações dividida pelo alcance, indicando o quão engajador o conteúdo foi proporcionalmente a quem o viu |

## 5. Limitações Técnicas e Como o Projeto Lida com Elas

- **Limite de requisições da API** (a Meta permite um número limitado de chamadas por hora): o sistema busca os dados em lotes programados, em vez de em tempo real, evitando estourar esse limite.
- **Tempo de retenção de dados da Meta**: a Meta não mantém o histórico de métricas indefinidamente. Por isso, o sistema salva cada coleta em seu próprio banco de dados, preservando o histórico mesmo depois que a Meta descarta os dados originais.
- **Aprovação da Meta para uso público**: gerenciar apenas a própria conta não exige revisão da Meta. Se o sistema for expandido para gerenciar contas de terceiros (ex: uma agência atendendo vários clientes), será necessário passar pelo processo de revisão de app (*App Review*) da Meta antes de liberar o acesso a esses usuários externos.

## 6. Público-Alvo e Benefício Principal

O **[Nome do Projeto]** é voltado para equipes de marketing, criadores de conteúdo e agências que gerenciam presença no Instagram e precisam entender o que funciona. O principal benefício é substituir a coleta manual e dispersa de números por um histórico centralizado e confiável, permitindo decisões de conteúdo baseadas em dados reais de engajamento ao longo do tempo, em vez de impressões pontuais.
