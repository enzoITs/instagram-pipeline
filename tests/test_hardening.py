"""Regression tests for the defects found in the September 2026 audit.

Each test names the behaviour that used to be wrong, so a future change that
reintroduces it fails here rather than in production.
"""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.config import Settings
from app.crypto import encrypt_token
from app.instagram.client import InstagramClient, get_shared_rate_limiter
from app.models import Account, Media, MetricSnapshot
from app.services.analytics import escape_like
from app.services.collector import collect_account
from app.services.jobs_runtime import sweep_stale_runs

GRAPH = "https://graph.instagram.com/v23.0"


class TestSecretKey:
    def test_an_empty_secret_is_refused_in_production(self) -> None:
        """`.env.example` used to ship SECRET_KEY= , deriving a public key."""
        with pytest.raises(ValueError, match="SECRET_KEY is empty"):
            Settings(_env_file=None, environment="production", secret_key="")

    def test_an_empty_secret_is_replaced_outside_production(self) -> None:
        settings = Settings(_env_file=None, environment="development", secret_key="")
        assert len(settings.secret_key) >= 32

    def test_a_malformed_encryption_key_is_rejected_at_startup(self) -> None:
        with pytest.raises(ValueError, match="valid Fernet key"):
            Settings(_env_file=None, token_encryption_key="not-a-fernet-key")


class TestSearchEscaping:
    def test_a_percent_sign_matches_a_literal_percent(self, http_client, account, db) -> None:
        """Searching "%" used to return every post instead of none."""
        db.add_all(
            [
                Media(account_id=account.id, ig_media_id="a", caption="sem simbolo"),
                Media(account_id=account.id, ig_media_id="b", caption="desconto de 50% hoje"),
            ]
        )
        db.commit()

        page = http_client.get(f"/api/accounts/{account.id}/media?search=%25").json()
        assert page["total"] == 1
        assert page["items"][0]["ig_media_id"] == "b"

    def test_an_underscore_is_not_a_wildcard(self, http_client, account, db) -> None:
        db.add_all(
            [
                Media(account_id=account.id, ig_media_id="a", caption="ab"),
                Media(account_id=account.id, ig_media_id="b", caption="a_b"),
            ]
        )
        db.commit()
        page = http_client.get(f"/api/accounts/{account.id}/media?search=a_b").json()
        assert page["total"] == 1

    def test_backslashes_survive(self) -> None:
        assert escape_like("a\\b") == "a\\\\b"


class TestCsvExport:
    def test_a_formula_caption_is_neutralised(self, http_client, account, db) -> None:
        """A caption is attacker-controlled text; Excel would run it."""
        media = Media(
            account_id=account.id,
            ig_media_id="danger",
            caption='=cmd|\'/c calc\'!A1',
        )
        db.add(media)
        db.flush()
        db.add(
            MetricSnapshot(
                media_id=media.id,
                collected_at=datetime.now(timezone.utc),
                likes=1,
            )
        )
        db.commit()

        body = http_client.get(f"/api/accounts/{account.id}/export.csv").text
        cell = list(csv.reader(body.splitlines()))[1][-1]
        # The cell still carries the original text, but no longer starts with a
        # character a spreadsheet reads as the beginning of a formula.
        assert cell.startswith("\t")
        assert "=cmd" in cell
        assert not cell.startswith("=")

    def test_the_export_is_produced_row_by_row(self, account, db) -> None:
        """It used to build the whole file in memory before replying."""
        from app.routers.system import export_csv

        media = Media(account_id=account.id, ig_media_id="m")
        db.add(media)
        db.flush()
        now = datetime.now(timezone.utc)
        db.add_all(
            [
                MetricSnapshot(
                    media_id=media.id, collected_at=now - timedelta(hours=n), likes=n
                )
                for n in range(5)
            ]
        )
        db.commit()

        response = export_csv(account=account, db=db, latest_only=False)

        async def drain() -> list[str]:
            return [chunk async for chunk in response.body_iterator]

        chunks = asyncio.run(drain())
        # One chunk for the header and one per snapshot, yielded lazily.
        assert len(chunks) == 6


class TestRateLimiter:
    def test_every_client_shares_one_window(self) -> None:
        """A per-client limiter reset the hourly budget on every collection."""
        first, second = InstagramClient(), InstagramClient()
        try:
            assert first.rate_limiter is second.rate_limiter
            assert first.rate_limiter is get_shared_rate_limiter()
        finally:
            first.close()
            second.close()


class TestInsightRounds:
    @respx.mock
    def test_metrics_are_dropped_one_at_a_time_until_the_call_succeeds(
        self, db, account, api_client
    ) -> None:
        """Meta rejects one metric per response; capping at 3 rounds lost data."""
        respx.get(f"{GRAPH}/{account.ig_user_id}").mock(
            return_value=httpx.Response(200, json={"id": account.ig_user_id})
        )
        respx.get(f"{GRAPH}/{account.ig_user_id}/media").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "m1",
                            "media_product_type": "FEED",
                            "like_count": 10,
                            "comments_count": 1,
                        }
                    ]
                },
            )
        )

        rejected = ["saved", "shares", "views", "total_interactions"]
        calls = {"n": 0}

        def insights(request):  # noqa: ANN001
            index = calls["n"]
            calls["n"] += 1
            if index < len(rejected):
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": f"(#100) metric[0] {rejected[index]} is not supported",
                            "code": 100,
                        }
                    },
                )
            return httpx.Response(
                200, json={"data": [{"name": "reach", "values": [{"value": 500}]}]}
            )

        respx.get(f"{GRAPH}/m1/insights").mock(side_effect=insights)

        result = collect_account(db, account, client=api_client)

        assert calls["n"] == len(rejected) + 1
        assert result.status.value == "success"
        snapshot = db.query(MetricSnapshot).one()
        assert snapshot.reach == 500  # would have been None before the fix

    @respx.mock
    def test_abandoning_insights_is_reported_as_a_partial_run(
        self, db, account, api_client
    ) -> None:
        """Returning no insights used to be recorded as a clean success."""
        respx.get(f"{GRAPH}/{account.ig_user_id}").mock(
            return_value=httpx.Response(200, json={"id": account.ig_user_id})
        )
        respx.get(f"{GRAPH}/{account.ig_user_id}/media").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"id": "m1", "media_product_type": "FEED", "like_count": 3}]},
            )
        )
        respx.get(f"{GRAPH}/m1/insights").mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": {
                        "message": (
                            "(#100) metric[0] reach, saved, shares, views, "
                            "total_interactions is not supported"
                        ),
                        "code": 100,
                    }
                },
            )
        )

        result = collect_account(db, account, client=api_client)

        assert result.status.value == "partial"
        assert "no insight metric is supported" in result.error


class TestResourceLeaks:
    def test_an_undecryptable_token_still_closes_the_http_client(self, db) -> None:
        """This path used to return without closing the connection pool."""
        account = Account(ig_user_id="leak", access_token_encrypted="garbage")
        db.add(account)
        db.commit()
        db.refresh(account)

        created: list[InstagramClient] = []
        original = InstagramClient.__init__

        def spy(self, *args, **kwargs):  # noqa: ANN001, ANN202
            original(self, *args, **kwargs)
            created.append(self)

        InstagramClient.__init__ = spy
        try:
            result = collect_account(db, account)
        finally:
            InstagramClient.__init__ = original

        assert result.status.value == "failed"
        assert created and all(client._client.is_closed for client in created)


class TestMediaOwnership:
    def test_two_accounts_can_hold_the_same_media_id(self, db) -> None:
        """An unscoped lookup let one account absorb another's snapshots."""
        first = Account(ig_user_id="1", access_token_encrypted=encrypt_token("t"))
        second = Account(ig_user_id="2", access_token_encrypted=encrypt_token("t"))
        db.add_all([first, second])
        db.commit()

        db.add(Media(account_id=first.id, ig_media_id="shared"))
        db.add(Media(account_id=second.id, ig_media_id="shared"))
        db.commit()  # the old global unique index rejected this

        rows = db.query(Media).filter(Media.ig_media_id == "shared").all()
        assert {row.account_id for row in rows} == {first.id, second.id}


class TestOrphanedRuns:
    def test_a_run_left_running_by_a_crash_is_closed(self, db, account) -> None:
        from datetime import datetime, timedelta, timezone

        from app.models import CollectionRun, RunStatus

        old = datetime.now(timezone.utc) - timedelta(hours=6)
        db.add(CollectionRun(account_id=account.id, status=RunStatus.RUNNING, started_at=old))
        db.commit()

        assert sweep_stale_runs() == 1

        db.expire_all()
        run = db.query(CollectionRun).one()
        assert run.status == RunStatus.FAILED
        assert run.finished_at is not None

    def test_a_collection_that_just_started_is_left_alone(self, db, account) -> None:
        """Workers run in their own processes now: a fresh `running` row almost
        always belongs to a collection that is very much alive."""
        from app.models import CollectionRun, RunStatus

        db.add(CollectionRun(account_id=account.id, status=RunStatus.RUNNING))
        db.commit()

        assert sweep_stale_runs() == 0
        db.expire_all()
        assert db.query(CollectionRun).one().status == RunStatus.RUNNING


class TestSecurityHeaders:
    def test_the_dashboard_is_served_with_a_policy(self, http_client) -> None:
        headers = http_client.get("/").headers
        assert headers["x-frame-options"] == "DENY"
        assert headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in headers["content-security-policy"]

    def test_the_api_docs_opt_out_of_the_policy(self, http_client) -> None:
        """Swagger UI ships an inline script the policy would block."""
        assert "content-security-policy" not in http_client.get("/docs").headers


class TestBackgroundCollection:
    def test_a_crashing_background_run_is_marked_failed(self, db, account, monkeypatch) -> None:
        """A crash must not leave the run stuck on `running` forever."""
        from app.models import CollectionRun, RunStatus
        from app.services import jobs_runtime

        run = CollectionRun(account_id=account.id, status=RunStatus.RUNNING, trigger="manual")
        db.add(run)
        db.commit()
        db.refresh(run)

        def explode(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            raise RuntimeError("boom")

        monkeypatch.setattr(jobs_runtime, "collect_account", explode)
        jobs_runtime.run_account_collection(account.id, run.id, None)

        db.expire_all()
        refreshed = db.get(CollectionRun, run.id)
        assert refreshed.status == RunStatus.FAILED
        assert "crashed" in refreshed.error

    def test_a_deleted_account_does_not_crash_the_worker(self, db) -> None:
        from app.services.jobs_runtime import run_account_collection

        run_account_collection(999999, 999999, None)  # must simply log and return
