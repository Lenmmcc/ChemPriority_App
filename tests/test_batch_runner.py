import time
import unittest

from src.batch_runner import run_ordered_batch


class BatchRunnerTests(unittest.TestCase):
    def test_parallel_results_keep_input_order(self):
        items = [0, 1, 2, 3, 4]

        def worker(item):
            time.sleep(0.01 * (4 - item))
            return item * 10

        results = run_ordered_batch(items, worker, max_workers=3, delay_seconds=0)

        self.assertEqual([result.value for result in results], [0, 10, 20, 30, 40])
        self.assertTrue(all(result.error is None for result in results))

    def test_parallel_worker_exception_is_returned_in_place(self):
        items = [0, 1, 2, 3, 4]

        def worker(item):
            if item == 2:
                raise RuntimeError("bad row")
            return item

        results = run_ordered_batch(items, worker, max_workers=3, delay_seconds=0)

        self.assertEqual([result.value for result in results], [0, 1, None, 3, 4])
        self.assertIsInstance(results[2].error, RuntimeError)
        self.assertEqual(str(results[2].error), "bad row")

    def test_progress_reports_completion_count(self):
        progress = []

        def worker(item):
            return item

        run_ordered_batch(
            ["a", "b", "c"],
            worker,
            max_workers=2,
            delay_seconds=0,
            progress_callback=lambda done, total, label: progress.append((done, total, label)),
            label_func=lambda item: item.upper(),
        )

        self.assertEqual([event[0] for event in progress], [1, 2, 3])
        self.assertTrue(all(event[1] == 3 for event in progress))

    def test_lifecycle_events_report_start_completion_and_failure(self):
        events = []

        def worker(item):
            if item == "bad":
                raise RuntimeError("bad row")
            return item.upper()

        run_ordered_batch(
            ["good", "bad", "last"],
            worker,
            max_workers=2,
            delay_seconds=0,
            event_callback=events.append,
            label_func=lambda item: item.title(),
        )

        self.assertEqual([event["event"] for event in events].count("started"), 3)
        self.assertEqual([event["event"] for event in events].count("completed"), 2)
        self.assertEqual([event["event"] for event in events].count("failed"), 1)
        self.assertEqual({event["index"] for event in events}, {0, 1, 2})
        self.assertTrue(all(event["total"] == 3 for event in events))
        self.assertTrue(all(event["elapsed_seconds"] >= 0 for event in events))

        failed = next(event for event in events if event["event"] == "failed")
        self.assertEqual(failed["label"], "Bad")
        self.assertEqual(failed["error"], "bad row")

        for index in range(3):
            item_events = [event["event"] for event in events if event["index"] == index]
            self.assertEqual(item_events[0], "started")
            self.assertIn(item_events[-1], {"completed", "failed"})

        active = 0
        max_active = 0
        for event in events:
            if event["event"] == "started":
                active += 1
                max_active = max(max_active, active)
            else:
                active -= 1
        self.assertLessEqual(max_active, 2)


if __name__ == "__main__":
    unittest.main()
