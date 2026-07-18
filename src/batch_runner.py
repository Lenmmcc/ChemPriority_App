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
    max_attempts=1,
    should_retry=None,
    retry_delay_seconds=0,
):
    items = list(items)
    total = len(items)
    results = [None] * total
    worker_count = max(1, int(max_workers or 1))
    delay = max(0.0, float(delay_seconds or 0))
    attempt_limit = max(1, int(max_attempts or 1))
    retry_delay = max(0.0, float(retry_delay_seconds or 0))
    finalized = set()
    finalized_count = 0

    def label_for(item):
        return label_func(item) if label_func else str(item)

    def record_progress(item):
        if progress_callback:
            progress_callback(finalized_count, total, label_for(item))

    def record_event(event, index, item, started_at, attempt, error=None):
        if not event_callback:
            return
        event_callback(
            {
                "event": event,
                "index": index,
                "total": total,
                "done": finalized_count,
                "label": label_for(item),
                "elapsed_seconds": max(0.0, time.monotonic() - started_at),
                "error": str(error) if error is not None else None,
                "attempt": attempt,
                "max_attempts": attempt_limit,
            }
        )

    def execute(index, attempt):
        item = items[index]
        started_at = time.monotonic()
        record_event("started", index, item, started_at, attempt)
        try:
            result = BatchResult(index=index, value=worker(item))
            event = "completed"
            error = None
        except Exception as exc:
            result = BatchResult(index=index, error=exc)
            event = "failed"
            error = exc
        return result, event, error, started_at

    def finish_attempt(index, attempt, result, event, error, started_at):
        nonlocal finalized_count
        item = items[index]
        results[index] = result
        record_event(event, index, item, started_at, attempt, error=error)
        will_retry = (
            attempt < attempt_limit
            and should_retry is not None
            and bool(should_retry(result))
        )
        if not will_retry and index not in finalized:
            finalized.add(index)
            finalized_count += 1
            record_progress(item)
        return will_retry

    def run_indices(indices, attempt):
        retry_indices = []
        if worker_count == 1 or len(indices) <= 1:
            for position, index in enumerate(indices):
                result, event, error, started_at = execute(index, attempt)
                if finish_attempt(
                    index, attempt, result, event, error, started_at
                ):
                    retry_indices.append(index)
                if delay and position < len(indices) - 1:
                    time.sleep(delay)
            return retry_indices

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_attempt = {}
            next_position = 0

            def submit(index):
                item = items[index]
                started_at = time.monotonic()
                record_event("started", index, item, started_at, attempt)
                future = executor.submit(worker, item)
                future_to_attempt[future] = (index, started_at)

            while next_position < len(indices) and len(future_to_attempt) < worker_count:
                submit(indices[next_position])
                next_position += 1
                if delay and next_position < len(indices):
                    time.sleep(delay)

            while future_to_attempt:
                future = next(as_completed(future_to_attempt))
                index, started_at = future_to_attempt.pop(future)
                try:
                    result = BatchResult(index=index, value=future.result())
                    event = "completed"
                    error = None
                except Exception as exc:
                    result = BatchResult(index=index, error=exc)
                    event = "failed"
                    error = exc
                if finish_attempt(
                    index, attempt, result, event, error, started_at
                ):
                    retry_indices.append(index)
                if next_position < len(indices):
                    submit(indices[next_position])
                    next_position += 1
                    if delay and next_position < len(indices):
                        time.sleep(delay)
        return sorted(retry_indices)

    pending = list(range(total))
    attempt = 1
    while pending:
        pending = run_indices(pending, attempt)
        if pending and retry_delay:
            time.sleep(retry_delay * attempt)
        attempt += 1

    return results
