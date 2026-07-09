import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from background_jobs import BackgroundJobCancelled, BackgroundJobRegistry  # noqa: E402


class BackgroundJobRegistryTests(unittest.TestCase):
    def test_function_job_succeeds_and_updates_stage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = BackgroundJobRegistry(Path(temp_dir))

            def work(context):
                context.update(stage="step-1", message="working")

            started = registry.start_function("test-success", work, job_id="job-success")
            self.assertEqual("job-success", started.job_id)

            status = self.wait_for_terminal(registry, "job-success")

            self.assertEqual("succeeded", status.status)
            self.assertEqual("step-1", status.stage)
            self.assertEqual("completed", status.message)
            self.assertIsNone(status.error)
            self.assertTrue(Path(status.log_path).exists())

    def test_function_job_failure_records_error_and_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = BackgroundJobRegistry(Path(temp_dir))

            def work(context):
                context.update(stage="boom")
                raise ValueError("bad input")

            registry.start_function("test-failure", work, job_id="job-failure")

            status = self.wait_for_terminal(registry, "job-failure")

            self.assertEqual("failed", status.status)
            self.assertEqual("boom", status.stage)
            self.assertIn("ValueError: bad input", status.error)
            self.assertIn("ValueError: bad input", Path(status.log_path).read_text(encoding="utf-8"))

    def test_request_cancel_sets_event_and_cancel_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = BackgroundJobRegistry(Path(temp_dir))

            def work(context):
                while True:
                    context.update(stage="looping")
                    if context.is_cancel_requested():
                        raise BackgroundJobCancelled("stopped")
                    time.sleep(0.01)

            registry.start_function("test-cancel", work, job_id="job-cancel")
            self.wait_for_status(registry, "job-cancel", "running")

            requested = registry.request_cancel("job-cancel")
            status = self.wait_for_terminal(registry, "job-cancel")

            self.assertEqual("cancel requested", requested.message)
            self.assertEqual("cancelled", status.status)
            self.assertEqual("stopped", status.message)
            self.assertTrue(Path(status.cancel_file).exists())

    def test_subprocess_job_writes_log_and_succeeds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = BackgroundJobRegistry(Path(temp_dir))

            registry.start_subprocess(
                "test-subprocess",
                [sys.executable, "-c", "print('hello from job')"],
                job_id="job-subprocess",
            )

            status = self.wait_for_terminal(registry, "job-subprocess")

            self.assertEqual("succeeded", status.status)
            self.assertEqual(0, status.returncode)
            self.assertIn("hello from job", Path(status.log_path).read_text(encoding="utf-8"))

    def test_subprocess_cancel_terminates_best_effort(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = BackgroundJobRegistry(Path(temp_dir))

            registry.start_subprocess(
                "test-subprocess-cancel",
                [sys.executable, "-c", "import time; time.sleep(10)"],
                job_id="job-subprocess-cancel",
            )
            self.wait_for_status(registry, "job-subprocess-cancel", "running")

            registry.request_cancel("job-subprocess-cancel")
            status = self.wait_for_terminal(registry, "job-subprocess-cancel")

            self.assertEqual("cancelled", status.status)
            self.assertTrue(Path(status.cancel_file).exists())

    def test_rejects_invalid_job_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = BackgroundJobRegistry(Path(temp_dir))

            with self.assertRaises(ValueError):
                registry.start_function("../bad", lambda context: None, job_id="../bad")

    def test_rejects_log_path_outside_log_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside_dir:
            registry = BackgroundJobRegistry(Path(temp_dir))

            with self.assertRaises(ValueError):
                registry.start_function(
                    "bad-log",
                    lambda context: None,
                    job_id="bad-log",
                    log_path=Path(outside_dir) / "job.log",
                )

    def wait_for_status(self, registry, job_id, expected, timeout=2):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = registry.get_status(job_id)
            if status.status == expected:
                return status
            time.sleep(0.01)
        self.fail(f"job {job_id} did not reach status {expected}; last={registry.get_status(job_id).status}")

    def wait_for_terminal(self, registry, job_id, timeout=3):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = registry.get_status(job_id)
            if status.status in {"succeeded", "failed", "cancelled"}:
                return status
            time.sleep(0.01)
        self.fail(f"job {job_id} did not finish; last={registry.get_status(job_id).status}")


if __name__ == "__main__":
    unittest.main()
