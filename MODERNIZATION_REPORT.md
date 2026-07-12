# Modernization of `retrying` (abandoned June 2016)

## Why this library

`retrying` (Apache 2.0, by Ray Holder) received its last commit on **June 6, 2016**, yet remains embedded in a decade of dependency chains and is still downloaded millions of times per month via legacy pins. Its successor (`tenacity`) exists, but the long tail of projects that still import `retrying` inherit its Python-2-era defects. This is a drop-in, API-compatible modernization.

## Defects found and fixed

1. **`KeyboardInterrupt` / `SystemExit` were caught and retried.** The retry loop used a bare `except:`, so Ctrl+C during an attempt was swallowed and the call retried instead of aborting. Fixed: `except Exception` — `BaseException` subclasses now propagate immediately. Verified: old code retried KeyboardInterrupt 5 times; new code raises on attempt 1.

2. **Wall-clock time used for deadlines.** `stop_max_delay` was computed from `time.time()`, so NTP adjustments or manual clock changes could corrupt or effectively disable the deadline. Fixed: `time.monotonic()`.

3. **Sleeps overshot the deadline.** The computed wait was never clamped to the remaining `stop_max_delay` budget. In the control test, a 200 ms deadline with `wait_fixed=10000` ran for a full 10 seconds on the old code; the new code clamps the sleep and finishes near the deadline. Clamping activates only when a delay deadline is actually configured, so pure attempt-count usage is unchanged.

4. **Traceback reference cycles.** Failed attempts stored raw `sys.exc_info()` tuples; the traceback→frame→locals cycle pinned attempt-local objects until a full GC pass. The discarded attempt is now explicitly dropped before sleeping, and the exception tuple is built from the exception object (`type(e), e, e.__traceback__`), preserving the historical `attempt.value` tuple shape for compatibility.

5. **`six` dependency removed.** `six.wraps` → `functools.wraps`; `six.reraise` → `raise exc.with_traceback(tb)`. One fewer transitive dependency across every environment that still installs this package.

6. **Float wait bounds crashed.** `wait_random_min/max` as floats crashed `random.randint`. Floats now use `random.uniform`; integer inputs preserve the exact historical `randint` semantics (verified by distribution test).

Also improved: the `@retry(...)` decorator now constructs its `Retrying` configuration once at decoration time instead of on every call; full type hints added; `__all__` defined.

## Deliberately NOT changed (compatibility)

- `exponential_sleep` still starts at `2**1` (the historical off-by-one); changing it would silently alter production backoff timing.
- The `stop`/`wait` string-name lookup (`getattr(self, stop)`) is preserved, including its quirks.
- `attempt.value` remains an `exc_info`-style 3-tuple on failure, since downstream filters index into it.
- Default `stop_max_delay=100` when explicitly selecting `stop="stop_after_delay"` is preserved.

## Verification

- **Legacy suite:** all 23 tests from the original 2016 `test_retrying.py` pass **unmodified** against the new module (no `six` installed).
- **Regression suite:** 11 new tests, one per fixed defect plus hygiene checks — all pass on the new code.
- **Control:** the same regression suite run against the original 1.3.x code fails 5 tests (interrupt retried ×2, deadline overshoot, six present, float crash), confirming each fix addresses a real, reproducible defect.

## Files

- `retrying.py` — modernized module, drop-in replacement, Python 3.8+
- `test_modernization.py` — regression suite (runs with plain `unittest`)

License remains Apache 2.0 with original copyright retained, as required.
