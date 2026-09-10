"""Tests for the background scheduler wiring."""

from __future__ import annotations

import pytest

from app import scheduler as scheduler_module
from app.config import Settings


class TestScheduler:
    def test_disabled_scheduler_does_not_start(self, monkeypatch) -> None:
        monkeypatch.setattr(
            scheduler_module, "get_settings", lambda: Settings(enable_scheduler=False)
        )
        assert scheduler_module.start_scheduler() is None
        assert scheduler_module.is_running() is False
        assert scheduler_module.next_collection_time() is None

    def test_starting_registers_both_jobs(self, monkeypatch) -> None:
        monkeypatch.setattr(
            scheduler_module,
            "get_settings",
            lambda: Settings(
                enable_scheduler=True,
                collection_interval_minutes=60,
                token_refresh_interval_hours=6,
            ),
        )
        scheduler = scheduler_module.start_scheduler()
        try:
            assert scheduler is not None
            assert scheduler_module.is_running() is True
            job_ids = {job.id for job in scheduler.get_jobs()}
            assert job_ids == {scheduler_module.COLLECT_JOB_ID, scheduler_module.REFRESH_JOB_ID}
            assert scheduler_module.next_collection_time() is not None
            # Calling start twice returns the same running scheduler.
            assert scheduler_module.start_scheduler() is scheduler
        finally:
            scheduler_module.shutdown_scheduler()

        assert scheduler_module.is_running() is False

    def test_a_failing_job_does_not_raise(self, monkeypatch) -> None:
        def explode(*_args, **_kwargs):
            raise RuntimeError("collection blew up")

        monkeypatch.setattr(scheduler_module, "collect_all_accounts", explode)
        monkeypatch.setattr(scheduler_module, "refresh_expiring_tokens", explode)

        # Both jobs swallow their errors so the scheduler keeps running.
        scheduler_module.run_collection_job()
        scheduler_module.run_token_refresh_job()


class TestJobLock:
    """The scheduler lives in the web process, so several workers would each
    fire the same job unless they agree through the database."""

    def test_the_lock_is_exclusive(self, db) -> None:
        from app.scheduler import job_lock

        with job_lock("demo") as first:
            assert first is True
            with job_lock("demo") as second:
                assert second is False

    def test_the_lock_is_released_afterwards(self, db) -> None:
        from app.scheduler import job_lock

        with job_lock("demo"):
            pass
        with job_lock("demo") as acquired:
            assert acquired is True

    def test_the_lock_is_released_even_when_the_job_raises(self, db) -> None:
        from app.scheduler import job_lock

        with pytest.raises(RuntimeError), job_lock("demo"):
            raise RuntimeError("job blew up")

        with job_lock("demo") as acquired:
            assert acquired is True

    def test_a_stale_lock_is_taken_over(self, db) -> None:
        """A worker that dies without releasing must not block the job forever."""
        from datetime import datetime, timedelta, timezone

        from app.models import JobLock
        from app.scheduler import job_lock

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        db.add(JobLock(job_id="demo", owner="dead-worker:1", acquired_at=past, expires_at=past))
        db.commit()

        with job_lock("demo") as acquired:
            assert acquired is True

    def test_a_locked_job_does_not_run_its_body(self, db, monkeypatch) -> None:
        from app import scheduler as scheduler_module

        calls = []
        monkeypatch.setattr(
            scheduler_module, "collect_all_accounts", lambda *a, **k: calls.append(1) or []
        )

        with scheduler_module.job_lock(scheduler_module.COLLECT_JOB_ID):
            scheduler_module.run_collection_job()

        assert calls == []
