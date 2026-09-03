#!/usr/bin/env python
"""Populate the database with realistic fake data.

Useful for exercising the dashboard before a real Meta app is approved:

    python scripts/seed_demo.py            # create the demo account
    python scripts/seed_demo.py --reset    # wipe it first

The seeded account is inactive by design, so the scheduler never tries to call
Meta with its fake token.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.crypto import encrypt_token  # noqa: E402
from app.database import init_db, session_scope  # noqa: E402
from app.instagram.metrics import compute_engagement_rate, total_interactions  # noqa: E402
from app.models import Account, CollectionRun, Media, MetricSnapshot, RunStatus  # noqa: E402

DEMO_IG_USER_ID = "demo-17841400000000000"

CAPTIONS = [
    "3 erros que travam o alcance do seu Reels (o segundo é o pior)",
    "Bastidores da gravação de hoje — spoiler: deu tudo errado",
    "Carrossel: o passo a passo que usamos para dobrar o engajamento",
    "Perguntas e respostas sobre marketing de conteúdo",
    "Antes e depois: o que mudou no nosso perfil em 90 dias",
    "Checklist de publicação que a nossa equipe segue toda semana",
    "O formato que mais salva no seu nicho não é o que você pensa",
    "Tutorial rápido: editando um Reels em 4 minutos",
    "Métricas que importam (e as que você pode ignorar)",
    "Respondendo o comentário mais repetido da semana",
    "Um erro de copy que custou 40% do alcance",
    "Como planejamos um mês inteiro de conteúdo em uma tarde",
]

PRODUCT_TYPES = ["REELS", "REELS", "REELS", "FEED", "FEED", "FEED", "FEED"]
MEDIA_TYPES = {"REELS": "VIDEO", "FEED": "IMAGE"}


def reset(db) -> None:
    account = db.scalar(select(Account).where(Account.ig_user_id == DEMO_IG_USER_ID))
    if account:
        db.delete(account)
        db.flush()
        print("Removed the existing demo account.")


def seed(db, *, posts: int, runs: int) -> None:
    now = datetime.now(timezone.utc)
    rng = random.Random(20260902)
    followers = rng.randint(9_000, 24_000)

    account = Account(
        ig_user_id=DEMO_IG_USER_ID,
        username="conta.demo",
        name="Conta Demo",
        account_type="MEDIA_CREATOR",
        followers_count=followers,
        media_count=posts,
        access_token_encrypted=encrypt_token("demo-token-not-valid"),
        token_expires_at=now + timedelta(days=59),
        # Inactive so the scheduler never calls Meta with this fake token.
        is_active=False,
        last_collected_at=now,
    )
    db.add(account)
    db.flush()

    collection_times = [now - timedelta(hours=6 * i) for i in range(runs)][::-1]

    for index in range(posts):
        product_type = rng.choice(PRODUCT_TYPES)
        published = now - timedelta(days=index * 2, hours=rng.randint(0, 20))
        media = Media(
            account_id=account.id,
            ig_media_id=f"demo-media-{index:04d}",
            media_type=MEDIA_TYPES[product_type],
            media_product_type=product_type,
            caption=CAPTIONS[index % len(CAPTIONS)],
            permalink=f"https://www.instagram.com/p/demo{index:04d}/",
            timestamp=published,
        )
        db.add(media)
        db.flush()

        # Baseline performance for this post, then growth over each collection.
        base_reach = rng.randint(1_200, 9_000) * (2 if product_type == "REELS" else 1)
        like_ratio = rng.uniform(0.04, 0.13)
        rate = None

        for step, collected_at in enumerate(collection_times):
            if collected_at < published:
                continue  # the post did not exist yet at that collection
            # Metrics grow quickly at first, then flatten out.
            growth = 1 - 0.55 ** (step + 1)
            noise = rng.uniform(0.95, 1.06)
            reach = int(base_reach * growth * noise)
            if reach <= 0:
                continue
            likes = int(reach * like_ratio * rng.uniform(0.9, 1.1))
            comments = max(0, int(likes * rng.uniform(0.02, 0.09)))
            saved = max(0, int(likes * rng.uniform(0.05, 0.25)))
            shares = max(0, int(likes * rng.uniform(0.03, 0.18)))
            views = int(reach * rng.uniform(1.1, 2.4)) if product_type == "REELS" else reach

            metrics = {
                "likes": likes, "comments": comments, "saved": saved,
                "shares": shares, "reach": reach, "views": views,
            }
            rate, basis = compute_engagement_rate(metrics, followers)

            db.add(
                MetricSnapshot(
                    media_id=media.id,
                    collected_at=collected_at,
                    **metrics,
                    total_interactions=total_interactions(metrics),
                    engagement_rate=rate,
                    engagement_basis=basis,
                    raw={"demo": True},
                )
            )

        media.last_snapshot_at = collection_times[-1]
        media.latest_engagement_rate = rate
        db.add(media)

    for step, collected_at in enumerate(collection_times):
        db.add(
            CollectionRun(
                account_id=account.id,
                started_at=collected_at,
                finished_at=collected_at + timedelta(seconds=rng.randint(20, 90)),
                status=RunStatus.SUCCESS,
                trigger="scheduled" if step else "manual",
                media_seen=posts,
                media_created=posts if step == 0 else 0,
                snapshots_created=posts,
                api_calls=posts + 2,
            )
        )

    print(
        f"Seeded demo account @{account.username}: {posts} posts, "
        f"{runs} collection runs, {followers} followers."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete the demo account first")
    parser.add_argument("--posts", type=int, default=24, help="how many posts to create")
    parser.add_argument("--runs", type=int, default=14, help="how many collection runs to simulate")
    args = parser.parse_args()

    init_db()
    with session_scope() as db:
        if args.reset:
            reset(db)
        existing = db.scalar(select(Account).where(Account.ig_user_id == DEMO_IG_USER_ID))
        if existing is not None:
            print("The demo account already exists. Re-run with --reset to recreate it.")
            return 0
        seed(db, posts=args.posts, runs=args.runs)
    print("Done. Start the app with: uvicorn app.main:app --reload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
