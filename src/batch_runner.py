from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import time


@dataclass
class BatchResult:
    index: int
    value: object = None
    error: Exception = None


def run_ordered_batch(
    items,
    worker,
    max_workers=1,
    delay_seconds=0,
    progress_callback=None,
    label_func=None,
):
    items = list(items)
    total = len(items)
    results = [None] * total
    worker_count = max(1, int(max_workers or 1))
    delay = max(0.0, float(delay_seconds or 0))

    def label_for(item):
        return label_func(item) if label_func else str(item)

    def record_progress(done, item):
        if progress_callback:
            progress_callback(done, total, label_for(item))

    if worker_count == 1 or total <= 1:
        for index, item in enumerate(items):
            try:
                results[index] = BatchResult(index=index, value=worker(item))
            except Exception as exc:
                results[index] = BatchResult(index=index, error=exc)
            record_progress(index + 1, item)
            if delay and index < total - 1:
                time.sleep(delay)
        return results

    completed = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_item = {}
        for index, item in enumerate(items):
            future = executor.submit(worker, item)
            future_to_item[future] = (index, item)
            if delay and index < total - 1:
                time.sleep(delay)

        for future in as_completed(future_to_item):
            index, item = future_to_item[future]
            try:
                results[index] = BatchResult(index=index, value=future.result())
            except Exception as exc:
                results[index] = BatchResult(index=index, error=exc)
            completed += 1
            record_progress(completed, item)

    return results
