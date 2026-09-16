import os
import time
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models import IntentCategory, SentimentLabel
from app.services import nlp_service
from app.services.nlp_service import (
    CommentAnalysis,
    NLPAnalyzer,
    doubt_strength,
    fallback_intent,
    fold,
    is_spam,
    lexicon_sentiment,
    rule_based_intent,
)

POS, NEG, NEU, DOUBT, SPAM = (
    SentimentLabel.POSITIVE,
    SentimentLabel.NEGATIVE,
    SentimentLabel.NEUTRAL,
    SentimentLabel.DOUBT,
    SentimentLabel.SPAM,
)


def lexicon_analyzer(**overrides) -> NLPAnalyzer:
    analyzer = NLPAnalyzer(Settings(nlp_backend="lexicon", **overrides))
    analyzer.load()
    return analyzer


# --------------------------------------------------------------------------- heuristics
def test_fold_strips_accents_and_case():
    assert fold("Incrível ÓTIMO Não") == "incrivel otimo nao"


@pytest.mark.parametrize(
    "text",
    [
        "Ganhe dinheiro em casa, link na bio 💰",
        "Sigam meu perfil que eu sigo de volta",
        "follow me for more check my page",
        "Confira https://promo.example.com agora",
        "Invista em bitcoin e ganhe 300% ao mês",
    ],
)
def test_spam_detection(text):
    assert is_spam(fold(text))


@pytest.mark.parametrize("text", ["Amei esse produto", "@maria olha isso", "Qual o preço?"])
def test_not_spam(text):
    assert not is_spam(fold(text))


def test_doubt_strength():
    assert doubt_strength("Isso é original?", fold("Isso é original?")) == 2
    assert doubt_strength("Vale a pena", fold("Vale a pena")) == 1
    assert doubt_strength("Amei!", fold("Amei!")) == 0


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Amei demais, perfeito!", POS),
        ("Que lindo 😍🔥", POS),
        ("Péssimo, horrível, nunca mais compro", NEG),
        ("Não gostei", NEG),
        ("não é ruim", POS),  # negation flips polarity
        ("Vi no stories", NEU),
        ("Muito caro 😡", NEG),
    ],
)
def test_lexicon_sentiment(text, label):
    predicted, confidence = lexicon_sentiment(text)
    assert predicted is label
    assert 0.5 <= confidence <= 0.95


@pytest.mark.parametrize(
    ("text", "label", "confidence", "intent"),
    [
        ("Ganhe dinheiro", SPAM, 0.9, IntentCategory.OTHER),
        ("Adorei! Quando vende?", POS, 0.95, IntentCategory.POTENTIAL_BUYER),
        ("Qual o valor do frete?", DOUBT, 0.8, IntentCategory.POTENTIAL_BUYER),
        ("Me manda o site por favor", NEU, 0.6, IntentCategory.POTENTIAL_BUYER),
        ("Comprei e veio com defeito", NEG, 0.9, None),  # buyer keywords ignored for negatives
        ("Isso é golpe, lixo", NEG, 0.9, IntentCategory.HATER),
        ("Isso é original?", DOUBT, 0.8, IntentCategory.QUESTION),
        ("Será que serve", NEU, 0.6, IntentCategory.QUESTION),
        ("Vocês abrem domingo?", NEU, 0.7, IntentCategory.QUESTION),
        ("Como vocês fazem isso", NEU, 0.7, IntentCategory.QUESTION),
        ("Como é lindo", POS, 0.9, None),  # exclamation, not a question
        ("Amei, vocês são demais né?", POS, 0.9, None),  # strongly positive rhetorical question
        ("Vi no stories", NEU, 0.7, None),
    ],
)
def test_rule_based_intent(text, label, confidence, intent):
    assert rule_based_intent(text, label, confidence) == intent


def test_fallback_intent():
    assert fallback_intent(POS, 0.9) is IntentCategory.PRAISE
    assert fallback_intent(POS, 0.5) is IntentCategory.OTHER
    assert fallback_intent(NEG, 0.9) is IntentCategory.HATER
    assert fallback_intent(NEG, 0.7) is IntentCategory.OTHER
    assert fallback_intent(NEU, 0.99) is IntentCategory.OTHER


def test_sentiment_score_property():
    assert CommentAnalysis(POS, 0.8, IntentCategory.PRAISE).sentiment_score == pytest.approx(0.9)
    assert CommentAnalysis(NEG, 0.8, IntentCategory.HATER).sentiment_score == pytest.approx(0.1)
    assert CommentAnalysis(DOUBT, 0.8, IntentCategory.QUESTION).sentiment_score == 0.5


# --------------------------------------------------------------------------- analyzer (lexicon)
def test_analyze_end_to_end_with_lexicon():
    texts = [
        "Produto incrível, amei demais",
        "Produto incrível, recomendo",
        "Produto incrível e entrega rápida",
        "Qual o preço? Quero comprar",
        "Péssimo atendimento, lixo",
        "Ganhe dinheiro em casa, link na bio",
        "Isso é original?",
        "",
    ]
    result = lexicon_analyzer().analyze(texts)
    assert result.backend == "lexicon"
    assert len(result.comments) == len(texts)
    labels = [analysis.sentiment for analysis in result.comments]
    assert labels[:3] == [POS, POS, POS]
    assert labels[5] is SPAM
    assert labels[6] is DOUBT
    assert result.comments[3].intent_category is IntentCategory.POTENTIAL_BUYER
    assert result.comments[4].intent_category is IntentCategory.HATER
    assert result.comments[7].key_phrases == []
    assert {"sentiment", "intent", "keywords_topics"} <= set(result.step_durations)

    top = result.topics[0]
    assert top.topic == "produto incrível"
    assert top.frequency == 3
    assert top.sentiment_avg > 0.7
    assert top.key_keywords[:2] == ["produto", "incrível"]
    assert all("ganhe" not in topic.topic for topic in result.topics)  # spam excluded from topics


def test_analyze_handles_empty_input():
    result = lexicon_analyzer().analyze([])
    assert result.comments == [] and result.topics == []


def test_tokenize_filters_noise():
    tokens = lexicon_analyzer().tokenize("KKKKK @maria #Novidade que produto INCRÍVEL 123 rsrs!!")
    assert tokens == ["novidade", "produto", "incrível"]


def test_extract_keywords_prefers_bigrams():
    analyzer = lexicon_analyzer()
    docs = [["produto", "incrível"], ["produto", "incrível"], ["entrega", "rápida"], []]
    keywords = analyzer.extract_keywords(docs)
    assert keywords[0] == ["produto incrível"]
    assert keywords[3] == []
    assert analyzer.extract_keywords([[], []]) == [[], []]


def test_extract_topics_deduplicates_overlapping_ngrams():
    analyzer = lexicon_analyzer()
    docs = [["melhor", "marca", "qualidade"]] * 12 + [["entrega", "rápida"]] * 4
    analyses = [CommentAnalysis(POS, 0.9, IntentCategory.PRAISE) for _ in docs]
    topics = analyzer.extract_topics(docs, analyses)
    assert len(topics) == 2  # the three words/bigrams of the repeated comment collapse into one topic
    assert topics[0].frequency == 12
    assert topics[1].topic in {"entrega rápida", "entrega", "rápida"}


def test_lexicon_performance_500_comments():
    texts = ["Produto incrível, quero comprar! Qual o preço?", "Péssimo, nunca mais", "@ana olha"] * 167
    started = time.perf_counter()
    result = lexicon_analyzer().analyze(texts)
    assert len(result.comments) == 501
    assert time.perf_counter() - started < 5


# --------------------------------------------------------------------------- analyzer (transformers, faked)
class FakeSentimentPipeline:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def __call__(self, texts, **kwargs):
        self.calls.append(kwargs)
        return [self.outputs[text] for text in texts]


class FakeZeroShot:
    def __init__(self, outputs):
        self.outputs = outputs
        self.received = []

    def __call__(self, texts, **kwargs):
        self.received.extend(texts)
        assert set(kwargs["candidate_labels"]) == set(nlp_service.ZERO_SHOT_LABELS)
        results = [{"labels": [self.outputs[t][0]], "scores": [self.outputs[t][1]]} for t in texts]
        return results[0] if len(results) == 1 else results


def test_transformers_backend_label_mapping_and_zero_shot_gating():
    buyer, hater, praise, neutral = list(nlp_service.ZERO_SHOT_LABELS)
    assert nlp_service.ZERO_SHOT_LABELS[hater] is IntentCategory.HATER
    sentiments = {
        "Preciso muito disso": {"label": "neutral", "score": 0.7},
        "Amei o produto": [{"label": "LABEL_2", "score": 0.9}, {"label": "LABEL_0", "score": 0.1}],
        "Que produto": {"label": "Positive", "score": 0.7},
        "Vocês pioraram": {"label": "negative", "score": 0.7},
        "Obrigado time": {"label": "5 stars", "score": 0.8},
        "Ganhe dinheiro rápido": {"label": "positive", "score": 0.99},
        "Onde compro?": {"label": "neutral", "score": 0.9},
        "hmm": {"label": "unknown-label", "score": 1.4},
    }
    zero_shot = {
        "Preciso muito disso": (buyer, 0.65),  # neutral + buyer >= 0.6 -> accepted
        "Amei o produto": (buyer, 0.8),  # positive + buyer < 0.85 -> rejected -> praise fallback
        "Que produto": (hater, 0.9),  # hater on positive comment -> rejected
        "Vocês pioraram": (praise, 0.99),  # praise on negative comment -> rejected
        "Obrigado time": (praise, 0.51),  # positive + praise >= 0.5 -> accepted
        "hmm": (neutral, 0.9),
    }
    pipeline = FakeSentimentPipeline(sentiments)
    zs = FakeZeroShot(zero_shot)
    analyzer = NLPAnalyzer(Settings(nlp_batch_size=16), sentiment_pipeline=pipeline, zero_shot_pipeline=zs)
    analyzer.load()  # no-op: pipelines injected
    assert analyzer.backend == "transformers"

    result = analyzer.analyze(list(sentiments))
    by_text = dict(zip(sentiments, result.comments))
    assert pipeline.calls[0]["batch_size"] == 16 and pipeline.calls[0]["truncation"] is True
    assert by_text["Preciso muito disso"].intent_category is IntentCategory.POTENTIAL_BUYER
    assert by_text["Amei o produto"].sentiment is POS
    assert by_text["Amei o produto"].intent_category is IntentCategory.PRAISE
    # Rejected zero-shot prediction -> sentiment fallback (positive 0.7 >= 0.55 -> praise).
    assert by_text["Que produto"].intent_category is IntentCategory.PRAISE
    assert by_text["Vocês pioraram"].intent_category is IntentCategory.OTHER
    assert by_text["Obrigado time"].intent_category is IntentCategory.PRAISE
    assert by_text["Ganhe dinheiro rápido"].sentiment is SPAM  # heuristics override the model
    assert by_text["Onde compro?"].intent_category is IntentCategory.POTENTIAL_BUYER
    assert by_text["hmm"].sentiment is NEU and by_text["hmm"].confidence == 1.0
    # Rules decided spam/buyer comments, so zero-shot only saw the undecided ones.
    assert "Ganhe dinheiro rápido" not in zs.received and "Onde compro?" not in zs.received


def test_warm_up_runs_a_tiny_inference_only_with_models():
    pipeline = FakeSentimentPipeline(
        {
            "Adorei o produto! Quanto custa?": {"label": "positive", "score": 0.9},
            "Não gostei, veio com defeito": {"label": "negative", "score": 0.9},
        }
    )
    NLPAnalyzer(Settings(nlp_intent_zero_shot=False), sentiment_pipeline=pipeline).warm_up()
    assert len(pipeline.calls) == 1
    lexicon_analyzer().warm_up()  # no models loaded: nothing to warm up


def test_single_zero_shot_output_dict_is_supported():
    zs = FakeZeroShot({"Preciso muito disso": (list(nlp_service.ZERO_SHOT_LABELS)[0], 0.9)})
    pipeline = FakeSentimentPipeline({"Preciso muito disso": {"label": "neutral", "score": 0.6}})
    result = NLPAnalyzer(Settings(), sentiment_pipeline=pipeline, zero_shot_pipeline=zs).analyze(["Preciso muito disso"])
    assert result.comments[0].intent_category is IntentCategory.POTENTIAL_BUYER


def test_inference_failures_degrade_to_lexicon_with_warnings():
    def broken(*_args, **_kwargs):
        raise RuntimeError("CUDA out of memory")

    analyzer = NLPAnalyzer(Settings(), sentiment_pipeline=broken, zero_shot_pipeline=broken)
    result = analyzer.analyze(["Amei demais", "Vi no stories"])
    assert result.comments[0].sentiment is POS
    assert len(result.warnings) == 2


def test_load_falls_back_to_lexicon_when_models_fail(monkeypatch):
    analyzer = NLPAnalyzer(Settings(nlp_backend="transformers"))

    def fail():
        raise OSError("model not found")

    monkeypatch.setattr(analyzer, "_load_transformers", fail)
    analyzer.load()
    analyzer.load()  # idempotent
    assert analyzer.backend == "lexicon"
    result = analyzer.analyze(["Amei"])
    assert any("léxico" in warning for warning in result.warnings)


def test_load_transformers_uses_pipeline_factory(monkeypatch):
    created = []
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False), device=lambda name: name,
                                 set_num_threads=lambda n: created.append(("threads", n)))
    fake_transformers = SimpleNamespace(pipeline=lambda task, **kwargs: created.append((task, kwargs)) or object())
    monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
    monkeypatch.setitem(__import__("sys").modules, "transformers", fake_transformers)

    analyzer = NLPAnalyzer(Settings(nlp_backend="transformers", nlp_torch_threads=3, nlp_intent_zero_shot=True))
    analyzer.load()
    assert analyzer.backend == "transformers"
    assert created[0] == ("threads", 3)
    assert created[1][0] == "text-classification" and created[1][1]["device"] == "cpu"
    assert created[2][0] == "zero-shot-classification"


@pytest.mark.parametrize(
    ("preference", "cuda", "expected"),
    [("auto", True, "cuda:0"), ("auto", False, "cpu"), ("cpu", True, "cpu"), ("cuda", False, "cpu")],
)
def test_resolve_device(preference, cuda, expected):
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda), device=lambda name: name)
    assert NLPAnalyzer(Settings(nlp_device=preference))._resolve_device(fake_torch) == expected


def test_get_nlp_analyzer_is_a_singleton():
    assert nlp_service.get_nlp_analyzer() is nlp_service.get_nlp_analyzer()


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("RUN_MODEL_TESTS") != "1", reason="set RUN_MODEL_TESTS=1 (downloads ~1.5 GB)")
def test_real_models_quality_and_speed():
    analyzer = NLPAnalyzer(Settings(nlp_backend="transformers"))
    analyzer.load()
    assert analyzer.backend == "transformers"
    samples = {
        "Perfeito! Melhor produto que já usei 👏": (POS, IntentCategory.PRAISE),
        "Pior compra que já fiz 😡": (NEG, IntentCategory.HATER),
        "Adorei! Quando vende?": (POS, IntentCategory.POTENTIAL_BUYER),
        "Isso é original?": (DOUBT, IntentCategory.QUESTION),
        "Sigam meu perfil que eu sigo de volta": (SPAM, IntentCategory.OTHER),
        "Vi no stories": (NEU, IntentCategory.OTHER),
    }
    texts = list(samples) * 17  # ~100 comments
    analyzer.analyze(texts[:4])  # warm-up
    started = time.perf_counter()
    result = analyzer.analyze(texts)
    assert time.perf_counter() - started < 10  # requirement: 100 comments < 10 s
    for text, analysis in zip(texts, result.comments):
        assert (analysis.sentiment, analysis.intent_category) == samples[text], text
