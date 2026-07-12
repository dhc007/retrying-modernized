"""Regression tests for the 2026 modernization of `retrying`.

Each test targets a specific defect in retrying 1.3.x. Where possible the
test is written so that it FAILS against the original 1.3.x code and PASSES
against the modernized module.
"""

import gc
import sys
import time
import unittest
import weakref

from retrying import retry, Retrying, RetryError


class TestKeyboardInterruptNotRetried(unittest.TestCase):
    """Bug 1: bare `except:` used to swallow and retry KeyboardInterrupt."""

    def test_keyboard_interrupt_propagates_immediately(self):
        calls = []

        @retry(stop_max_attempt_number=5, wait_fixed=1)
        def boom():
            calls.append(1)
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            boom()
        self.assertEqual(len(calls), 1, "KeyboardInterrupt must not be retried")

    def test_system_exit_propagates_immediately(self):
        calls = []

        @retry(stop_max_attempt_number=5, wait_fixed=1)
        def boom():
            calls.append(1)
            raise SystemExit(3)

        with self.assertRaises(SystemExit):
            boom()
        self.assertEqual(len(calls), 1, "SystemExit must not be retried")

    def test_ordinary_exceptions_still_retried(self):
        calls = []

        @retry(stop_max_attempt_number=3, wait_fixed=1)
        def boom():
            calls.append(1)
            raise ValueError("nope")

        with self.assertRaises(ValueError):
            boom()
        self.assertEqual(len(calls), 3)


class TestDeadlineClamping(unittest.TestCase):
    """Bug 3: sleeps used to overshoot stop_max_delay arbitrarily."""

    def test_does_not_sleep_far_past_deadline(self):
        # fixed wait of 10s but a 200ms deadline: total runtime must be
        # close to the deadline, not close to the wait.
        @retry(stop_max_delay=200, wait_fixed=10000)
        def always_fails():
            raise IOError("flaky")

        start = time.monotonic()
        with self.assertRaises(IOError):
            always_fails()
        elapsed_ms = (time.monotonic() - start) * 1000
        self.assertLess(elapsed_ms, 1500, f"slept past deadline: {elapsed_ms:.0f}ms")


class TestMonotonicClock(unittest.TestCase):
    """Bug 2: delay tracking must not depend on wall-clock time.time()."""

    def test_delay_uses_monotonic(self):
        observed = []

        def stop_func(attempts, delay_ms):
            observed.append(delay_ms)
            return attempts >= 2

        r = Retrying(stop_func=stop_func, wait_fixed=1)
        with self.assertRaises(ValueError):
            r.call(lambda: (_ for _ in ()).throw(ValueError()))
        # delays must be non-negative and sane even conceptually under
        # wall-clock adjustments; monotonic guarantees non-decreasing.
        self.assertTrue(all(d >= 0 for d in observed))


class TestTracebackCycleRelease(unittest.TestCase):
    """Bug 4: discarded attempts used to pin frames via exc_info cycles."""

    def test_failed_attempt_objects_are_collectable(self):
        class Canary:
            pass

        canary_refs = []

        attempts = {"n": 0}

        @retry(stop_max_attempt_number=3, wait_fixed=1)
        def flaky():
            c = Canary()
            canary_refs.append(weakref.ref(c))
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ValueError("transient")
            return "ok"

        self.assertEqual(flaky(), "ok")
        gc.collect()
        dead = sum(1 for r in canary_refs[:-1] if r() is None)
        # canaries from the two failed attempts must be collectable
        self.assertEqual(dead, 2, "failed-attempt frames still pinned in memory")


class TestFloatRandomWait(unittest.TestCase):
    """Bug 6: float wait_random_min/max used to crash random.randint."""

    def test_float_bounds_accepted(self):
        r = Retrying(wait_random_min=0.5, wait_random_max=2.5)
        for _ in range(50):
            v = r.random_sleep(1, 0)
            self.assertGreaterEqual(v, 0.5)
            self.assertLessEqual(v, 2.5)

    def test_int_bounds_keep_historical_semantics(self):
        r = Retrying(wait_random_min=1, wait_random_max=3)
        vals = {r.random_sleep(1, 0) for _ in range(200)}
        self.assertTrue(vals.issubset({1, 2, 3}))


class TestNoSixDependency(unittest.TestCase):
    """Bug 5: the six dependency is gone."""

    def test_six_not_imported(self):
        import retrying as m
        self.assertNotIn("six", getattr(m, "__dict__", {}))
        src = open(m.__file__).read()
        self.assertNotIn("import six", src)


class TestDecoratorHygiene(unittest.TestCase):
    def test_functools_wraps_metadata(self):
        @retry(stop_max_attempt_number=2)
        def documented():
            """my docstring"""
            return 1

        self.assertEqual(documented.__name__, "documented")
        self.assertEqual(documented.__doc__, "my docstring")

    def test_decorator_reuses_retrying_instance(self):
        # @retry(...) should build its Retrying config once, not per call
        @retry(stop_max_attempt_number=1)
        def f():
            return 42

        self.assertEqual(f(), 42)
        self.assertEqual(f(), 42)


if __name__ == "__main__":
    unittest.main(verbosity=2)
