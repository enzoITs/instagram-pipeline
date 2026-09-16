"""Deterministic synthetic Instagram data (SCRAPER_MODE=mock).

Used for local demos without Apify credentials and for end-to-end tests. Every
generated post carries `source="mock"` and a warning, and the UI flags it, so demo
numbers are never mistaken for real metrics.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.instagram_url import InstagramURL
from app.services.scraper_service import ScrapedComment, ScrapedPost

MOCK_WARNING = "Dados de demonstração gerados localmente (SCRAPER_MODE=mock); não refletem o post real."

_TEMPLATES: dict[str, tuple[float, list[str]]] = {
    "praise": (
        0.38,
        [
            "Que vídeo incrível! 😍",
            "Amei demais esse conteúdo 🔥🔥",
            "Perfeito! Melhor produto que já usei",
            "Parabéns pelo trabalho, ficou sensacional 👏",
            "Simplesmente maravilhoso ❤️",
            "Top demais!! Qualidade absurda",
            "Esse produto é incrível, recomendo muito",
            "Arrasou como sempre 😍",
            "Lindo demais, amei a cor nova",
            "Conteúdo de qualidade, sempre entregando 👏👏",
            "Que edição linda, parabéns pela produção!",
            "Produto incrível, mudou minha rotina",
            "Melhor marca, qualidade impecável",
            "Adorei o lançamento, ficou perfeito",
        ],
    ),
    "buyer": (
        0.15,
        [
            "Quanto custa? Quero muito!",
            "Onde compro esse produto?",
            "Tem link pra comprar?",
            "Quero um! Entrega em Belo Horizonte?",
            "Qual o preço? Aceita pix?",
            "Ainda tem disponível no tamanho M?",
            "Vocês enviam para Portugal?",
            "Tem cupom de desconto? 😍",
            "Adorei! Quando vende?",
            "Comprei ontem e já quero outro",
            "Qual o valor do frete para SP?",
            "Parcela no cartão? Quero comprar",
        ],
    ),
    "doubt": (
        0.12,
        [
            "Como funciona a garantia?",
            "Será que serve pra pele oleosa?",
            "Alguém já testou? Funciona mesmo?",
            "Qual a diferença pro modelo anterior?",
            "Dura quanto tempo a bateria?",
            "Isso é original?",
            "Não entendi, precisa de assinatura?",
            "Vale a pena mesmo?",
        ],
    ),
    "negative": (
        0.10,
        [
            "Muito caro pelo que entrega",
            "Péssimo atendimento, nunca mais compro",
            "Comprei e veio com defeito, que decepção",
            "Propaganda enganosa, não funciona",
            "Horrível, dinheiro jogado fora",
            "Achei o vídeo muito longo e chato",
            "Isso é golpe, cuidado gente",
            "Pior compra que já fiz 😡",
            "Ridículo esse preço",
        ],
    ),
    "neutral": (
        0.17,
        [
            "@mariana_s olha isso",
            "@joao.pedro vem ver",
            "Primeiro comentário",
            "Vi no stories",
            "Assistindo de novo",
            "Marcando a @carol_lima",
            "Salvei pra ver depois",
            "Alguém mais vendo em 2026?",
        ],
    ),
    "spam": (
        0.08,
        [
            "Ganhe dinheiro em casa, link na bio 💰",
            "Sigam meu perfil que eu sigo de volta",
            "Confira meu perfil para promoções incríveis!!",
            "Quer ganhar seguidores? Chama no direct",
            "Invista em bitcoin e ganhe 300% ao mês, chama no WhatsApp",
            "follow me for more 🔥 check my page",
        ],
    ),
}

_FIRST_NAMES = [
    "ana", "bruno", "carla", "diego", "elisa", "felipe", "gabi", "hugo", "isa", "joao",
    "karina", "lucas", "marina", "nina", "otavio", "paula", "rafa", "sofia", "thiago", "vivi",
]
_SUFFIXES = ["", "_", ".", "oficial", "_br", "123", "22", "x", "_art", "fit"]
_EMOJI_TAILS = ["", "", "", " 🔥", " 😍", " 👏", " ❤️", " 🙌", "!!"]


def generate_mock_post(parsed: InstagramURL, max_comments: int = 500) -> ScrapedPost:
    rng = random.Random(parsed.shortcode)  # same URL -> same data (reproducible demos/tests)
    published_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=rng.randint(0, 60 * 24 * 200))

    n_comments = min(rng.randint(180, 320), max_comments)
    categories = list(_TEMPLATES)
    weights = [_TEMPLATES[name][0] for name in categories]

    comments: list[ScrapedComment] = []
    for index in range(n_comments):
        category = rng.choices(categories, weights=weights, k=1)[0]
        text = rng.choice(_TEMPLATES[category][1])
        if category in {"praise", "buyer"}:
            text += rng.choice(_EMOJI_TAILS)
        author = f"{rng.choice(_FIRST_NAMES)}{rng.choice(_SUFFIXES)}{rng.randint(0, 999)}"
        likes = int(rng.paretovariate(1.6)) - 1 if category != "spam" else 0
        comments.append(
            ScrapedComment(
                comment_id=f"mock_{parsed.shortcode}_{index}"[:64],
                text=text,
                author=author,
                likes=min(likes, 5000),
                created_at=published_at + timedelta(minutes=rng.randint(1, 60 * 72)),
            )
        )

    views = rng.randint(40_000, 600_000)
    likes = int(views * rng.uniform(0.03, 0.11))
    return ScrapedPost(
        instagram_id=parsed.shortcode,
        url=parsed.canonical_url,
        media_type=parsed.media_type,
        source="mock",
        caption="Novo lançamento chegou! 🚀 Conta nos comentários o que achou #novidade #lancamento",
        creator_username="loja.demo",
        thumbnail_url=None,
        published_at=published_at,
        duration_sec=float(rng.randint(12, 90)) if parsed.media_type == "reel" else None,
        views=views,
        likes=likes,
        comments_count=n_comments + rng.randint(0, 400),
        shares=None,
        saves=None,
        comments=comments,
        warnings=[MOCK_WARNING],
    )
