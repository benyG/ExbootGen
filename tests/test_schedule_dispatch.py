import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app import dispatch_due_schedules


class DispatchDueSchedulesTest(unittest.TestCase):
    def test_skips_completed_entries(self):
        now = datetime.now()
        past_day = (now.date() - timedelta(days=1)).isoformat()
        past_time = (now - timedelta(minutes=10)).time().isoformat(timespec="minutes")
        future_day = (now.date() + timedelta(days=1)).isoformat()

        entries = [
            {"id": "queued-1", "day": past_day, "time": past_time, "status": "queued"},
            {"id": "done-1", "day": past_day, "time": past_time, "status": "succeeded"},
            {"id": "failed-1", "day": past_day, "time": past_time, "status": "failed"},
            {"id": "running-1", "day": past_day, "time": past_time, "status": "running"},
            {"id": "future-queued", "day": future_day, "time": past_time, "status": "queued"},
        ]

        with (
            patch("app.db.get_schedule_entries", return_value=entries),
            patch("app._enqueue_schedule_job", return_value={"status": "queued", "job_id": "job-123", "mode": "inline"})
            as enqueue_mock,
            patch("app.db.update_schedule_status") as update_status_mock,
        ):
            summary = dispatch_due_schedules()

        enqueue_mock.assert_called_once_with(past_day, [entries[0]])
        update_status_mock.assert_called_once_with(["queued-1"], "queued")
        self.assertEqual(summary["dispatched_batches"], 1)
        self.assertEqual(summary["dispatched_entries"], 1)


if __name__ == "__main__":
    unittest.main()
