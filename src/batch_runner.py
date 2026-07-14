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
    event_callback=None,
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

    def record_event(event, index, item, done, started_at, error=None):
        if not event_callback:
            return
        event_callback(
            {
                "event": event,
                "index": index,
                "total": total,
                "done": done,
                "label": label_for(item),
                "elapsed_seconds": max(0.0, time.monotonic() - started_at),
                "error": str(error) if error is not None else None,
            }
        )

    if worker_count == 1 or total <= 1:
        for index, item in enumerate(items):
            started_at = time.monotonic()
            record_event("started", index, item, index, started_at)
            try:
                results[index] = BatchResult(index=index, value=worker(item))
                event = "completed"
                error = None
            except Exception as exc:
                results[index] = BatchResult(index=index, error=exc)
                event = "failed"
                error = exc
            record_event(event, index, item, index + 1, started_at, error=error)
            record_progress(index + 1, item)
            if delay and index < total - 1:
                time.sleep(delay)
        return results

    completed = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_item = {}
        next_index = 0

        def submit(index):
            item = items[index]
            started_at = time.monotonic()
            record_event("started", index, item, completed, started_at)
            future = executor.submit(worker, item)
            future_to_item[future] = (index, item, started_at)
            if delay and index < total - 1:
                time.sleep(delay)

        while next_index < total and len(future_to_item) < worker_count:
            submit(next_index)
            next_index += 1

        while future_to_item:
            future = next(as_completed(future_to_item))
            index, item, started_at = future_to_item[future]
            del future_to_item[future]
            try:
                results[index] = BatchResult(index=index, value=future.result())
                event = "completed"
                error = None
            except Exception as exc:
                results[index] = BatchResult(index=index, error=exc)
                event = "failed"
                error = exc
            completed += 1
            record_event(event, index, item, completed, started_at, error=error)
            record_progress(completed, item)
            if next_index < total:
                submit(next_index)
                next_index += 1

    return results
