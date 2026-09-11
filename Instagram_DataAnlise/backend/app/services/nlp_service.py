"""Comment NLP: sentiment (5 labels), intent (5 categories), keywords and topics.

Design
------
* Sentiment: a multilingual transformer (positive/neutral/negative) combined with
  high-precision heuristics for the two labels no public model provides:
  `spam` (links, follow-for-follow, get-rich-quick) and `doubt` (questions/uncertainty
  without clear polarity).
* Intent: keyword rules first (cheap and precise for buyers, spam, insults and
  questions), zero-shot NLI only for comments the rules cannot decide.
* Keywords: TF-IDF over unigrams+bigrams with PT/EN stopwords.
* Topics: document-frequency aggregation of n-grams, with per-topic sentiment.

If transformers/torch are unavailable or a model fails, the analyzer degrades to a
PT/EN lexicon backend instead of failing the analysis.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from nltk.tokenize import TweetTokenizer
from sklearn.feature_extraction.text import TfidfVectorizer

from app.config import Settings, get_settings
from app.models import IntentCategory, SentimentLabel

logger = logging.getLogger(__name__)

Pipeline = Callable[..., Any]


# --------------------------------------------------------------------------- text utils
def fold(text: str) -> str:
    """Lowercase and strip accents ("Incrível" -> "incrivel") for matching."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_BASE_STOPWORDS = """
a o e é de da do das dos em no na nos nas um uma uns umas por para pra pro pros pras com sem
que se ao aos à às ou mas mais menos muito muita muitos muitas pouco já não sim só também tb tbm
eu tu ele ela nós vós eles elas você vocês vc vcs voce voces me te lhe nos vos lhes meu minha meus
minhas seu sua seus suas nosso nossa isso isto esse essa esses essas este esta aquele aquela aqui
ali lá la ai aí então entao quando como onde qual quais quanto porque pq por quê ser ter estar
foi era sou são é está estou estão tá ta to tô vai vou vamos fica ficou tem têm tinha ser seria
sobre até ate depois antes ainda assim bem tão tao toda todo todos todas algo alguém alguem cada
the and or of to in on at for with is are was were be been it this that these those you your i me my
we our they them their he she his her its an as by from so not no yes do does did have has had will
just very can could would should what when where why how which who whom there here all any some
pelo pela pelos pelas num numa dum duma demais quer fiz faz fazer feito vez vezes coisa coisas
kkk kkkk kkkkk rs rsrs haha hahaha hehe né ne q oq eh hein hj hoje ja la
"""
_LAUGH_REGEX = re.compile(r"^(k{2,}|(rs){2,}|(ha){2,}h?|(he){2,}|(hu){2,}|(ja){2,})$")
_WORD_REGEX = re.compile(r"^[^\W\d_]{3,}$")


@lru_cache
def stopwords() -> frozenset[str]:
    words = {fold(word) for word in _BASE_STOPWORDS.split()}
    try:  # enrich with NLTK's corpus when it has been downloaded (Docker image does it)
        from nltk.corpus import stopwords as nltk_stopwords

        for language in ("portuguese", "english"):
            words.update(fold(word) for word in nltk_stopwords.words(language))
    except LookupError:
        pass
    return frozenset(words)


# --------------------------------------------------------------------------- lexicons
POSITIVE_TERMS = frozenset(
    fold(word)
    for word in """
    amei adorei amo incrível incríveis perfeito perfeita maravilhoso maravilhosa maravilha lindo linda
    lindos lindas top sensacional parabéns melhor melhores ótimo ótima excelente show arrasou recomendo
    fantástico fantástica espetacular gostei apaixonada apaixonado sucesso impecável obrigada obrigado
    feliz fofo fofa brabo braba perfeição genial massa lindeza love loved amazing perfect beautiful best
    great awesome nice good excellent wonderful
    """.split()
)
NEGATIVE_TERMS = frozenset(
    fold(word)
    for word in """
    péssimo péssima horrível horrorosa horroroso ruim lixo odeio odiei decepção decepcionado decepcionada
    golpe fraude enganosa enganoso enganação defeito pior piores ridículo ridícula nojo nojento vergonha
    chato chata mentira mentiroso fake triste raiva porcaria terrível quebrou caro caríssimo
    bad worst hate terrible awful scam ugly boring disappointed disappointing
    """.split()
)
INSULT_TERMS = frozenset(
    fold(word)
    for word in """
    lixo ridículo ridícula golpe golpista fraude nojo nojento vergonha idiota burro burra palhaço
    palhaçada otário otária lixoso bosta merda porcaria fake mentiroso cringe incompetente incompetentes
    scam trash stupid idiot
    """.split()
)
NEGATIVE_PHRASES = tuple(
    fold(phrase)
    for phrase in (
        "nunca mais", "jogado fora", "não funciona", "não gostei", "não recomendo", "propaganda enganosa",
        "veio com defeito", "muito caro", "caro demais", "perda de tempo", "não vale", "deixa a desejar",
    )
)
POSITIVE_PHRASES = tuple(
    fold(phrase) for phrase in ("muito bom", "muito boa", "super recomendo", "vale cada centavo", "nota 10")
)
NEGATIONS = frozenset({"nao", "nunca", "nem", "jamais", "not", "never", "no"})
POSITIVE_EMOJIS = frozenset("😍❤🔥👏🙌😊🥰💖💕👍💯✨🤩😘💙💜🖤💗😻🥳")
NEGATIVE_EMOJIS = frozenset("😡🤬👎😠😤💩🤮😒🙄😞😢😭")

_SPAM_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"https?://", r"\bwww\.", r"\bbit\.ly\b", r"\bwa\.me\b", r"link na bio", r"sigo de volta", r"\bsdv\b",
        r"\bsigam?\b.{0,20}\b(meu|minha|perfil|pagina)\b", r"follow (me|back|4 ?follow)",
        r"check (out )?my (page|profile)",
        r"confira (o )?meu perfil", r"visite (o )?meu perfil", r"ganh(e|ar) dinheiro", r"renda extra",
        r"ganhar seguidores", r"seguidores gratis", r"compr(e|ar) seguidores", r"\bbitcoin\b", r"\bcripto",
        r"\bforex\b", r"ganh(e|a) \d+ ?%", r"chama no (whats|zap)", r"lucro garantido", r"trabalhe de casa",
    )
]
_QUESTION_START = re.compile(
    r"^(como|quando|onde|quanto|quanta|quantos|quantas|qual|quais|porque|por que|pq|sera|alguem|tem como|da pra|"
    r"how|what|when|where|why|which|does|is it|can i|do you)\b"
)
_DOUBT_PHRASES = tuple(
    fold(phrase)
    for phrase in (
        "será que", "não sei se", "tenho dúvida", "dúvida", "não entendi", "funciona mesmo", "é confiável",
        "vale a pena", "é original", "alguém já testou", "alguém já usou", "alguém sabe",
    )
)
_BUYER_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"\bquanto (custa|e|sai|fica|ta|esta)\b", r"\bqual (o |e o )?(preco|valor)\b", r"\bpreco\b", r"\bvalor\b",
        r"\bonde (compr|encontr|vend|acho|adquir)", r"\bquero (comprar|um|uma|o meu|a minha|esse|essa|pedir)\b",
        r"\bquero\b.{0,15}\b(esse|essa|kit|produto)\b", r"\bpreciso (disso|desse|dessa|de um|de uma)\b",
        r"\bme (manda|passa|envia) (o |a )?(site|link|contato|valor|preco|loja)\b",
        r"\bvou comprar\b", r"\bcomprei\b", r"\bcomprar\b", r"\btem link\b", r"\blink\b.{0,20}\bcompr",
        r"\bentrega(m)?\b", r"\bfrete\b", r"\benvi(a|am) (para|pra)\b", r"\bparcel", r"\bdisponive(l|is)\b",
        r"\btamanho\b", r"\bcupom\b", r"\bdesconto\b", r"\bpix\b", r"\bquando (vende|chega|lanca|sai|volta)\b",
        r"\ba venda\b", r"\bencomend", r"\bhow much\b", r"\bprice\b", r"\bwhere (can i|to) (buy|get)\b",
        r"\bshipping\b", r"\bbuy\b",
    )
]

# Zero-shot is only a second opinion for comments the keyword rules cannot decide.
# Benchmarks on PT-BR comments (CPU, 256 texts): multilingual MiniLM-L6 took 1.3 s but is
# noisy (e.g. any mention of "produto" leans to "buy"); mDeBERTa-v3-base was more accurate
# on praise but took 78 s, breaking the <60 s NLP budget. Both over-predict a "question"
# label, so questions are left to the rules and predictions are gated by sentiment below.
ZERO_SHOT_LABELS: dict[str, IntentCategory] = {
    "interesse em comprar o produto": IntentCategory.POTENTIAL_BUYER,
    "ofensa ou crítica agressiva": IntentCategory.HATER,
    "elogio": IntentCategory.PRAISE,
    "comentário neutro": IntentCategory.OTHER,
}
ZERO_SHOT_TEMPLATE = "Este comentário expressa {}."
# Minimum zero-shot score per (predicted intent, comment sentiment); missing pairs are rejected.
ZERO_SHOT_MIN_SCORE: dict[tuple[IntentCategory, SentimentLabel], float] = {
    (IntentCategory.POTENTIAL_BUYER, SentimentLabel.NEUTRAL): 0.6,
    (IntentCategory.POTENTIAL_BUYER, SentimentLabel.POSITIVE): 0.85,
    (IntentCategory.HATER, SentimentLabel.NEGATIVE): 0.5,
    (IntentCategory.HATER, SentimentLabel.NEUTRAL): 0.6,
    (IntentCategory.PRAISE, SentimentLabel.POSITIVE): 0.5,
    (IntentCategory.PRAISE, SentimentLabel.NEUTRAL): 0.6,
    (IntentCategory.OTHER, SentimentLabel.NEUTRAL): 0.5,
}

_MODEL_LABELS: dict[str, SentimentLabel] = {
    "positive": SentimentLabel.POSITIVE,
    "pos": SentimentLabel.POSITIVE,
    "negative": SentimentLabel.NEGATIVE,
    "neg": SentimentLabel.NEGATIVE,
    "neutral": SentimentLabel.NEUTRAL,
    "neu": SentimentLabel.NEUTRAL,
    # Unnamed 3-class heads follow the (negative, neutral, positive) convention.
    "label_0": SentimentLabel.NEGATIVE,
    "label_1": SentimentLabel.NEUTRAL,
    "label_2": SentimentLabel.POSITIVE,
    # 5-star review models.
    "1 star": SentimentLabel.NEGATIVE,
    "2 stars": SentimentLabel.NEGATIVE,
    "3 stars": SentimentLabel.NEUTRAL,
    "4 stars": SentimentLabel.POSITIVE,
    "5 stars": SentimentLabel.POSITIVE,
}


# --------------------------------------------------------------------------- results
@dataclass(slots=True)
class CommentAnalysis:
    sentiment: SentimentLabel
    confidence: float
    intent_category: IntentCategory
    key_phrases: list[str] = field(default_factory=list)

    @property
    def sentiment_score(self) -> float:
        """0 = negative, 0.5 = neutral/doubt, 1 = positive (spam is excluded upstream)."""
        if self.sentiment is SentimentLabel.POSITIVE:
            return 0.5 + 0.5 * self.confidence
        if self.sentiment is SentimentLabel.NEGATIVE:
            return 0.5 - 0.5 * self.confidence
        return 0.5


@dataclass(slots=True)
class TopicResult:
    topic: str
    frequency: int
    sentiment_avg: float
    key_keywords: list[str]


@dataclass(slots=True)
class NLPResult:
    comments: list[CommentAnalysis]
    topics: list[TopicResult]
    backend: str
    step_durations: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- analyzer
class NLPAnalyzer:
    """Loads models once per process and analyzes comments in batches."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        sentiment_pipeline: Pipeline | None = None,
        zero_shot_pipeline: Pipeline | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._sentiment_pipeline = sentiment_pipeline
        self._zero_shot_pipeline = zero_shot_pipeline
        self._loaded = sentiment_pipeline is not None
        self._backend = "transformers" if sentiment_pipeline is not None else "lexicon"
        self._lock = threading.Lock()
        self._tokenizer = TweetTokenizer(preserve_case=False, reduce_len=True, strip_handles=True)
        self._warnings: list[str] = []

    # ------------------------------------------------------------------ lifecycle
    @property
    def backend(self) -> str:
        return self._backend

    def load(self) -> None:
        """Idempotent, thread-safe model loading (called at worker start-up)."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            if self.settings.nlp_backend == "transformers":
                started = time.perf_counter()
                try:
                    self._load_transformers()
                    self._backend = "transformers"
                    logger.info(
                        "nlp_models_loaded",
                        extra={
                            "sentiment_model": self.settings.sentiment_model,
                            "intent_model": self.settings.intent_model if self._zero_shot_pipeline else None,
                            "duration_ms": round((time.perf_counter() - started) * 1000),
                        },
                    )
                except Exception as exc:  # noqa: BLE001 - any load failure degrades to lexicon
                    self._sentiment_pipeline = None
                    self._zero_shot_pipeline = None
                    self._backend = "lexicon"
                    self._warnings.append("Modelos de NLP indisponíveis; análise feita com léxico (menor precisão).")
                    logger.exception("nlp_model_load_failed", extra={"error": type(exc).__name__})
            else:
                self._backend = "lexicon"
            self._loaded = True

    def _load_transformers(self) -> None:
        import torch
        from transformers import pipeline

        if self.settings.nlp_torch_threads:
            torch.set_num_threads(self.settings.nlp_torch_threads)
        device = self._resolve_device(torch)
        # transformers 5 warns about an "incorrect regex pattern" for this XLM-R tokenizer.
        # Verified to be a false positive: the default tokenization matches the model's
        # original SentencePiece output, while `fix_mistral_regex=True` would break it.
        self._sentiment_pipeline = pipeline(
            "text-classification", model=self.settings.sentiment_model, device=device
        )
        if self.settings.nlp_intent_zero_shot:
            self._zero_shot_pipeline = pipeline(
                "zero-shot-classification", model=self.settings.intent_model, device=device
            )

    def _resolve_device(self, torch: Any) -> Any:
        preference = self.settings.nlp_device
        cuda_available = bool(torch.cuda.is_available())
        if preference == "cuda" and not cuda_available:
            logger.warning("nlp_cuda_unavailable_falling_back_to_cpu")
        use_cuda = cuda_available and preference != "cpu"
        return torch.device("cuda:0" if use_cuda else "cpu")

    # ------------------------------------------------------------------ public API
    def analyze(self, texts: Sequence[str]) -> NLPResult:
        self.load()
        warnings = list(self._warnings)
        durations: dict[str, float] = {}
        texts = [" ".join((text or "").split()) for text in texts]

        started = time.perf_counter()
        sentiments = self.analyze_sentiment(texts, warnings=warnings)
        durations["sentiment"] = time.perf_counter() - started

        started = time.perf_counter()
        intents = self.classify_intents(texts, sentiments, warnings=warnings)
        durations["intent"] = time.perf_counter() - started

        started = time.perf_counter()
        token_docs = [self.tokenize(text) for text in texts]
        keywords = self.extract_keywords(token_docs)
        analyses = [
            CommentAnalysis(sentiment=label, confidence=round(conf, 4), intent_category=intent, key_phrases=phrases)
            for (label, conf), intent, phrases in zip(sentiments, intents, keywords)
        ]
        topics = self.extract_topics(token_docs, analyses)
        durations["keywords_topics"] = time.perf_counter() - started

        logger.info(
            "nlp_analysis_completed",
            extra={
                "comments": len(texts),
                "backend": self.backend,
                "durations_ms": {name: round(value * 1000) for name, value in durations.items()},
            },
        )
        return NLPResult(
            comments=analyses,
            topics=topics,
            backend=self.backend,
            step_durations={name: round(value, 3) for name, value in durations.items()},
            warnings=warnings,
        )

    def analyze_sentiment(
        self, texts: Sequence[str], *, warnings: list[str] | None = None
    ) -> list[tuple[SentimentLabel, float]]:
        """Return (label, confidence) per text using the 5-label scheme."""
        base = self._model_sentiment(texts, warnings)
        results: list[tuple[SentimentLabel, float]] = []
        for text, (label, confidence) in zip(texts, base):
            folded = fold(text)
            if is_spam(folded):
                results.append((SentimentLabel.SPAM, 0.9))
                continue
            strength = doubt_strength(text, folded)
            polar = label in (SentimentLabel.POSITIVE, SentimentLabel.NEGATIVE)
            if strength and (not polar or confidence < 0.65):
                results.append((SentimentLabel.DOUBT, 0.6 + 0.1 * strength))
                continue
            results.append((label, confidence))
        return results

    def classify_intents(
        self,
        texts: Sequence[str],
        sentiments: Sequence[tuple[SentimentLabel, float]],
        *,
        warnings: list[str] | None = None,
    ) -> list[IntentCategory]:
        intents: list[IntentCategory | None] = []
        undecided: list[int] = []
        for index, (text, (label, confidence)) in enumerate(zip(texts, sentiments)):
            intent = rule_based_intent(text, label, confidence)
            intents.append(intent)
            if intent is None:
                undecided.append(index)

        if undecided and self._zero_shot_pipeline is not None:
            predictions = self._zero_shot(texts, undecided, warnings)
            for index, (predicted, score) in predictions.items():
                label, _ = sentiments[index]
                min_score = ZERO_SHOT_MIN_SCORE.get((predicted, label))
                if min_score is not None and score >= min_score:
                    intents[index] = predicted

        final: list[IntentCategory] = []
        for index, intent in enumerate(intents):
            if intent is None:
                label, confidence = sentiments[index]
                intent = fallback_intent(label, confidence)
            final.append(intent)
        return final

    def tokenize(self, text: str) -> list[str]:
        tokens: list[str] = []
        stop = stopwords()
        for token in self._tokenizer.tokenize(text):
            token = token.lstrip("#")
            if not _WORD_REGEX.match(token):
                continue
            folded = fold(token)
            if folded in stop or _LAUGH_REGEX.match(folded):
                continue
            tokens.append(token)
        return tokens

    @staticmethod
    def ngrams(tokens: Sequence[str]) -> list[str]:
        return list(tokens) + [f"{a} {b}" for a, b in zip(tokens, tokens[1:]) if a != b]

    def extract_keywords(self, token_docs: Sequence[Sequence[str]], top_k: int = 3) -> list[list[str]]:
        """Top TF-IDF n-grams per comment (bigrams absorb their own unigrams)."""
        if not any(token_docs):
            return [[] for _ in token_docs]
        vectorizer = TfidfVectorizer(analyzer=self.ngrams, sublinear_tf=True, norm="l2")
        matrix = vectorizer.fit_transform(token_docs).tocsr()
        features = vectorizer.get_feature_names_out()

        keywords: list[list[str]] = []
        for row_index in range(matrix.shape[0]):
            start, end = matrix.indptr[row_index], matrix.indptr[row_index + 1]
            ranked = sorted(
                zip(matrix.indices[start:end], matrix.data[start:end]),
                key=lambda item: (-item[1], features[item[0]]),
            )
            chosen: list[str] = []
            for feature_index, _score in ranked:
                term = str(features[feature_index])
                words = term.split()
                # Skip terms already covered by a chosen bigram; a bigram replaces its unigrams.
                if any(set(words) <= set(existing.split()) for existing in chosen):
                    continue
                chosen = [existing for existing in chosen if not (len(words) > 1 and existing in words)]
                chosen.append(term)
                if len(chosen) >= top_k:
                    break
            keywords.append(chosen)
        return keywords

    def extract_topics(
        self,
        token_docs: Sequence[Sequence[str]],
        analyses: Sequence[CommentAnalysis],
        max_topics: int = 10,
    ) -> list[TopicResult]:
        eligible = [
            index
            for index, analysis in enumerate(analyses)
            if analysis.sentiment is not SentimentLabel.SPAM and token_docs[index]
        ]
        if not eligible:
            return []
        doc_grams = {index: set(self.ngrams(token_docs[index])) for index in eligible}
        document_frequency: Counter[str] = Counter()
        for grams in doc_grams.values():
            document_frequency.update(grams)

        min_df = 2 if len(eligible) >= 10 else 1
        candidates = sorted(
            document_frequency.items(),
            key=lambda item: (-item[1], 0 if " " in item[0] else 1, item[0]),
        )
        selected: list[str] = []
        members_of: dict[str, set[int]] = {}
        for gram, frequency in candidates:
            if frequency < min_df:
                break
            gram_members = {index for index in eligible if gram in doc_grams[index]}
            # Two topics covering (almost) the same comments are the same conversation.
            conflict = next(
                (
                    chosen
                    for chosen in selected
                    if len(gram_members & members_of[chosen]) / min(len(gram_members), len(members_of[chosen]))
                    >= 0.8
                ),
                None,
            )
            if conflict is None:
                selected.append(gram)
            elif " " in gram and conflict in gram.split() and frequency >= 0.6 * len(members_of[conflict]):
                # A phrase that captures most uses of a word is more informative ("produto incrível").
                selected[selected.index(conflict)] = gram
            else:
                continue
            members_of[gram] = gram_members
            if len(selected) >= max_topics:
                break

        topics: list[TopicResult] = []
        for gram in selected:
            members = sorted(members_of[gram])
            words = gram.split()
            co_occurring: Counter[str] = Counter()
            for index in members:
                co_occurring.update(token for token in set(token_docs[index]) if token not in words)
            keywords = words + [token for token, _ in co_occurring.most_common(max(0, 5 - len(words)))]
            sentiment_avg = sum(analyses[index].sentiment_score for index in members) / len(members)
            topics.append(
                TopicResult(
                    topic=gram[:128],
                    frequency=len(members),
                    sentiment_avg=round(sentiment_avg, 3),
                    key_keywords=keywords,
                )
            )
        topics.sort(key=lambda topic: (-topic.frequency, topic.topic))
        return topics

    # ------------------------------------------------------------------ backends
    def _model_sentiment(
        self, texts: Sequence[str], warnings: list[str] | None
    ) -> list[tuple[SentimentLabel, float]]:
        if self._sentiment_pipeline is not None and texts:
            try:
                outputs = self._sentiment_pipeline(
                    list(texts), batch_size=self.settings.nlp_batch_size, truncation=True, max_length=256
                )
                return [self._parse_model_output(output) for output in outputs]
            except Exception as exc:  # noqa: BLE001 - inference failure degrades to lexicon
                logger.exception("sentiment_inference_failed", extra={"error": type(exc).__name__})
                if warnings is not None:
                    warnings.append("Falha no modelo de sentimento; resultado calculado com léxico.")
        return [lexicon_sentiment(text) for text in texts]

    @staticmethod
    def _parse_model_output(output: Any) -> tuple[SentimentLabel, float]:
        if isinstance(output, list):  # top_k=None style output: pick the best label
            output = max(output, key=lambda item: item["score"])
        label = _MODEL_LABELS.get(str(output["label"]).strip().lower(), SentimentLabel.NEUTRAL)
        return label, float(max(0.0, min(1.0, output["score"])))

    def _zero_shot(
        self, texts: Sequence[str], indices: Sequence[int], warnings: list[str] | None
    ) -> dict[int, tuple[IntentCategory, float]]:
        assert self._zero_shot_pipeline is not None
        try:
            outputs = self._zero_shot_pipeline(
                [texts[index] for index in indices],
                candidate_labels=list(ZERO_SHOT_LABELS),
                hypothesis_template=ZERO_SHOT_TEMPLATE,
                multi_label=False,
                batch_size=self.settings.nlp_batch_size,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("zero_shot_inference_failed", extra={"error": type(exc).__name__})
            if warnings is not None:
                warnings.append("Falha na classificação zero-shot de intenção; usando regras.")
            return {}
        if isinstance(outputs, dict):
            outputs = [outputs]
        predictions: dict[int, tuple[IntentCategory, float]] = {}
        for index, output in zip(indices, outputs):
            if output["labels"] and output["labels"][0] in ZERO_SHOT_LABELS:
                predictions[index] = (ZERO_SHOT_LABELS[output["labels"][0]], float(output["scores"][0]))
        return predictions


# --------------------------------------------------------------------------- heuristics
def is_spam(folded: str) -> bool:
    return any(pattern.search(folded) for pattern in _SPAM_PATTERNS)


def doubt_strength(text: str, folded: str) -> int:
    """0 = no doubt signal, 1 = one signal, 2 = question mark plus interrogative/doubt wording."""
    has_question_mark = "?" in text
    has_wording = bool(_QUESTION_START.match(folded)) or any(phrase in folded for phrase in _DOUBT_PHRASES)
    return int(has_question_mark) + int(has_wording)


def _words(folded: str) -> list[str]:
    return re.findall(r"[^\W\d_]+", folded)


def lexicon_sentiment(text: str) -> tuple[SentimentLabel, float]:
    folded = fold(text)
    words = _words(folded)
    score = 0.0
    negate_window = 0
    for word in words:
        if word in NEGATIONS:
            negate_window = 2
            continue
        polarity = 1 if word in POSITIVE_TERMS else -1 if word in NEGATIVE_TERMS else 0
        if polarity:
            score += -polarity if negate_window else polarity
        negate_window = max(0, negate_window - 1)
    score -= sum(1.5 for phrase in NEGATIVE_PHRASES if phrase in folded)
    score += sum(1.0 for phrase in POSITIVE_PHRASES if phrase in folded)
    score += 0.75 * sum(1 for ch in text if ch in POSITIVE_EMOJIS)
    score -= 0.75 * sum(1 for ch in text if ch in NEGATIVE_EMOJIS)

    if score >= 1:
        return SentimentLabel.POSITIVE, min(0.95, 0.6 + 0.08 * score)
    if score <= -1:
        return SentimentLabel.NEGATIVE, min(0.95, 0.6 + 0.08 * abs(score))
    return SentimentLabel.NEUTRAL, 0.6


def rule_based_intent(text: str, label: SentimentLabel, confidence: float) -> IntentCategory | None:
    """High-precision keyword rules. Returns None when rules cannot decide."""
    folded = fold(text)
    if label is SentimentLabel.SPAM:
        return IntentCategory.OTHER
    if label is not SentimentLabel.NEGATIVE and any(pattern.search(folded) for pattern in _BUYER_PATTERNS):
        return IntentCategory.POTENTIAL_BUYER
    if any(word in INSULT_TERMS for word in _words(folded)):
        return IntentCategory.HATER
    if label is SentimentLabel.DOUBT or any(phrase in folded for phrase in _DOUBT_PHRASES):
        return IntentCategory.QUESTION
    strongly_positive = label is SentimentLabel.POSITIVE and confidence >= 0.8
    if "?" in text and not strongly_positive:
        return IntentCategory.QUESTION
    # "Como é lindo!" starts like a question but is an exclamation: only neutral text counts.
    if _QUESTION_START.match(folded) and label is SentimentLabel.NEUTRAL:
        return IntentCategory.QUESTION
    return None


def fallback_intent(label: SentimentLabel, confidence: float) -> IntentCategory:
    if label is SentimentLabel.POSITIVE and confidence >= 0.55:
        return IntentCategory.PRAISE
    # Plain complaints stay "other"; only strongly negative comments count as haters.
    if label is SentimentLabel.NEGATIVE and confidence >= 0.85:
        return IntentCategory.HATER
    return IntentCategory.OTHER


@lru_cache
def get_nlp_analyzer() -> NLPAnalyzer:
    """Process-wide analyzer (models are expensive: load once, reuse)."""
    return NLPAnalyzer()
