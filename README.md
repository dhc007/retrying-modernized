# retrying-modernized

A drop-in, API-compatible modernization of the classic Python [`retrying`](https://github.com/rholder/retrying) library, which has been unmaintained since **June 2016** but is still downloaded millions of times per month through legacy dependency chains.

## What was fixed

Six real defects were found in an audit of retrying 1.3.x:

1. **`KeyboardInterrupt` / `SystemExit` were caught and retried.** A bare `except:` meant Ctrl+C during an attempt was swallowed and the call retried instead of aborting. Now uses `except Exception` so these propagate immediately.
2. **Wall-clock time used for deadlines.** `stop_max_delay` was computed from `time.time()`, so NTP/clock adjustments could corrupt the deadline. Now uses `time.monotonic()`.
3. **Sleeps overshot the deadline.** Waits were never clamped to the remaining `stop_max_delay` budget. A 200 ms deadline with `wait_fixed=10000` ran for a full **10 seconds** on the old code. Sleeps are now clamped to the deadline.
4. **Traceback reference cycles.** Failed attempts stored raw `sys.exc_info()` tuples, pinning attempt-local objects in memory. Discarded attempts are now released promptly.
5. **The `six` dependency is removed.** Zero dependencies; Python 3.8+.
6. **Float wait bounds crashed.** `wait_random_min/max` as floats crashed `random.randint`. Floats now work via `random.uniform`; integer inputs keep the exact historical behavior.

Bonus: the `@retry(...)` decorator now builds its configuration once at decoration time instead of on every call, and the module is fully type-hinted.

## Compatibility

- All **23 tests from the original 2016 test suite pass unmodified** against this module.
- A new regression suite (`test_modernization.py`, 11 tests) covers each fixed defect. Run against the *original* 1.3.x code as a control, it fails 5 of them — confirming each fix addresses a real, reproducible bug.
- Deliberate non-changes for compatibility: the historical `exponential_sleep` off-by-one is preserved (changing it would silently alter production backoff timing), `attempt.value` remains an `exc_info`-style 3-tuple, and the string-name `stop`/`wait` lookup behaves as before.

## Usage

Identical to the original:

```python
from retrying import retry

@retry(stop_max_attempt_number=3, wait_fixed=2000)
def flaky_call():
    ...
```

To migrate an existing project, replace the old `retrying.py` with this one. No code changes needed.

## Running the tests

```
python3 -m unittest test_modernization -v
```

## License

Apache License 2.0. Original copyright 2013-2014 Ray Holder is retained in the source header, as required by the license. This is an independent community modernization; if you're starting a brand-new project, also consider [`tenacity`](https://github.com/jd/tenacity), the actively maintained successor.
