"""Tests for the background scheduler wiring."""

from __future__ import annotations

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
