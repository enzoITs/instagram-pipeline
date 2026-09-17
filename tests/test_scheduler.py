"""Tests for the periodic jobs: what beat publishes, and the database lock.

The schedule runs in Celery beat, in its own process, so the lock these jobs
take is the only thing stopping two workers from collecting the same account
twice and spending the Meta quota twice.
"""

from __future__ import annotations

import pytest

from app import scheduler as scheduler_module
from app.config import Settings


class TestBeatSchedule:
    def test_disabled_scheduler_publishes_nothing(self) -> None:
        assert scheduler_module.beat_schedule(Settings(enable_scheduler=False)) == {}

    def test_every_periodic_job_is_registered(self) -> None:
        schedule = scheduler_module.beat_schedule(
            Settings(
                enable_scheduler=True,
                collection_interval_minutes=60,
                token_refresh_interval_hours=6,
                tracking_interval_minutes=120,
            )
        )
        assert set(schedule) == {
            scheduler_module.COLLECT_JOB_ID,
            scheduler_module.REFRESH_JOB_ID,
            scheduler_module.SWEEP_JOB_ID,
            scheduler_module.TRACKING_JOB_ID,
        }
        assert schedule[scheduler_module.COLLECT_JOB_ID]["schedule"] == 3600
        assert schedule[scheduler_module.REFRESH_JOB_ID]["schedule"] == 21600
        assert schedule[scheduler_module.TRACKING_JOB_ID]["schedule"] == 7200
        # Every entry names a task the worker actually registers.
        from app import tasks  # noqa: F401  (registers them on the Celery app)
        from app.celery_app import celery_app

        for entry in schedule.values():
            assert entry["task"] in celery_app.tasks

    def test_tracking_can_be_switched_off_on_its_own(self) -> None:
        schedule = scheduler_module.beat_schedule(
            Settings(enable_scheduler=True, tracking_interval_minutes=0)
        )
        assert scheduler_module.TRACKING_JOB_ID not in schedule

    def test_next_collection_is_estimated_from_the_last_scheduled_run(self, db, account) -> None:
        from datetime import datetime, timedelta, timezone

        from app.models import CollectionRun, RunStatus

        started = datetime.now(timezone.utc) - timedelta(minutes=30)
        db.add(
            CollectionRun(
                account_id=account.id,
                status=RunStatus.SUCCESS,
                trigger="scheduled",
                started_at=started,
            )
        )
        db.commit()

        settings = scheduler_module.get_settings()
        original = settings.enable_scheduler
        settings.enable_scheduler = True
        try:
            expected = started + timedelta(minutes=settings.collection_interval_minutes)
            assert scheduler_module.next_collection_time() == expected
        finally:
            settings.enable_scheduler = original

    def test_no_estimate_without_a_scheduled_run(self, db) -> None:
        assert scheduler_module.next_collection_time() is None


class TestJobs:
    def test_a_failing_job_does_not_raise(self, db, monkeypatch) -> None:
        def explode(*_args, **_kwargs):
            raise RuntimeError("collection blew up")

        monkeypatch.setattr(scheduler_module, "collect_all_accounts", explode)
        monkeypatch.setattr(scheduler_module, "refresh_expiring_tokens", explode)

        # Both jobs swallow their errors so the worker keeps consuming the queue.
        assert scheduler_module.run_collection_job() == 0
        assert scheduler_module.run_token_refresh_job() == 0

    def test_a_locked_job_does_not_run_its_body(self, db, monkeypatch) -> None:
        calls: list[int] = []
        monkeypatch.setattr(
            scheduler_module, "collect_all_accounts", lambda *a, **k: calls.append(1) or []
        )

        with scheduler_module.job_lock(scheduler_module.COLLECT_JOB_ID):
            assert scheduler_module.run_collection_job() == 0

        assert calls == []


class TestJobLock:
    def test_the_lock_is_exclusive(self, db) -> None:
        with scheduler_module.job_lock("demo") as first:
            assert first is True
            with scheduler_module.job_lock("demo") as second:
                assert second is False

    def test_the_lock_is_released_afterwards(self, db) -> None:
        with scheduler_module.job_lock("demo"):
            pass
        with scheduler_module.job_lock("demo") as acquired:
            assert acquired is True

    def test_the_lock_is_released_even_when_the_job_raises(self, db) -> None:
        with pytest.raises(RuntimeError), scheduler_module.job_lock("demo"):
            raise RuntimeError("job blew up")

        with scheduler_module.job_lock("demo") as acquired:
            assert acquired is True

    def test_a_stale_lock_is_taken_over(self, db) -> None:
        """A worker that dies without releasing must not block the job forever."""
        from datetime import datetime, timedelta, timezone

        from app.models import JobLock

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        db.add(JobLock(job_id="demo", owner="dead-worker:1", acquired_at=past, expires_at=past))
        db.commit()

        with scheduler_module.job_lock("demo") as acquired:
            assert acquired is True
