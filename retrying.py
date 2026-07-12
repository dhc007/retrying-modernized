## Copyright 2013-2014 Ray Holder
## Modernization 2026 (community revival, API-compatible)
##
## Licensed under the Apache License, Version 2.0 (the "License");
## you may not use this file except in compliance with the License.
## You may obtain a copy of the License at
##
## http://www.apache.org/licenses/LICENSE-2.0
##
## Unless required by applicable law or agreed to in writing, software
## distributed under the License is distributed on an "AS IS" BASIS,
## WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
## See the License for the specific language governing permissions and
## limitations under the License.

"""Drop-in modernization of the classic ``retrying`` library.

Public API is 100% compatible with retrying 1.3.x. Changes are internal:

* Python 3.8+ only; the ``six`` dependency is gone.
* ``KeyboardInterrupt`` / ``SystemExit`` are no longer caught and retried
  (bare ``except:`` replaced with ``except Exception``).
* Elapsed-time tracking uses ``time.monotonic()``; wall-clock (NTP) jumps
  can no longer corrupt ``stop_max_delay``.
* Sleeps are clamped to the remaining ``stop_max_delay`` budget, so a
  retry loop no longer sleeps past its own deadline.
* Traceback reference cycles are broken after each failed attempt is
  discarded, removing a memory-retention footgun.
* ``wait_random_min`` / ``wait_random_max`` accept floats.
* Full type hints.
"""

from __future__ import annotations

import functools
import random
import time
import traceback
from typing import Any, Callable, Optional, Tuple, Type, Union

__all__ = ["retry", "Retrying", "Attempt", "RetryError", "MAX_WAIT"]

# Kept identical to the historical value (sys.maxint / 2 on py2).
MAX_WAIT = 1073741823

_StopFunc = Callable[[int, int], bool]
_WaitFunc = Callable[[int, int], float]
_ExcInfo = Tuple[Type[BaseException], BaseException, Any]


def _retry_if_exception_of_type(
    retryable_types: Union[Type[BaseException], Tuple[Type[BaseException], ...]],
) -> Callable[[BaseException], bool]:
    def _retry_if_exception_these_types(exception: BaseException) -> bool:
        return isinstance(exception, retryable_types)

    return _retry_if_exception_these_types


def retry(*dargs: Any, **dkw: Any) -> Callable[..., Any]:
    """Decorator that retries the wrapped function on failure.

    Supports both ``@retry`` and ``@retry(...)`` syntax. All keyword
    arguments are forwarded to :class:`Retrying`.
    """
    # support both @retry and @retry() as valid syntax
    if len(dargs) == 1 and callable(dargs[0]):

        def wrap_simple(f: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(f)
            def wrapped_f(*args: Any, **kw: Any) -> Any:
                return Retrying().call(f, *args, **kw)

            return wrapped_f

        return wrap_simple(dargs[0])

    else:

        def wrap(f: Callable[..., Any]) -> Callable[..., Any]:
            retrying = Retrying(*dargs, **dkw)

            @functools.wraps(f)
            def wrapped_f(*args: Any, **kw: Any) -> Any:
                return retrying.call(f, *args, **kw)

            return wrapped_f

        return wrap


class Retrying:
    def __init__(
        self,
        stop: Optional[str] = None,
        wait: Optional[str] = None,
        stop_max_attempt_number: Optional[int] = None,
        stop_max_delay: Optional[float] = None,
        wait_fixed: Optional[float] = None,
        wait_random_min: Optional[float] = None,
        wait_random_max: Optional[float] = None,
        wait_incrementing_start: Optional[float] = None,
        wait_incrementing_increment: Optional[float] = None,
        wait_incrementing_max: Optional[float] = None,
        wait_exponential_multiplier: Optional[float] = None,
        wait_exponential_max: Optional[float] = None,
        retry_on_exception: Optional[
            Union[Callable[[BaseException], bool], Tuple[Type[BaseException], ...]]
        ] = None,
        retry_on_result: Optional[Callable[[Any], bool]] = None,
        wrap_exception: bool = False,
        stop_func: Optional[_StopFunc] = None,
        wait_func: Optional[_WaitFunc] = None,
        wait_jitter_max: Optional[float] = None,
        before_attempts: Optional[Callable[[int], None]] = None,
        after_attempts: Optional[Callable[[int], None]] = None,
    ) -> None:

        self._stop_max_attempt_number = 5 if stop_max_attempt_number is None else stop_max_attempt_number
        self._stop_max_delay = 100 if stop_max_delay is None else stop_max_delay
        self._wait_fixed = 1000 if wait_fixed is None else wait_fixed
        self._wait_random_min = 0 if wait_random_min is None else wait_random_min
        self._wait_random_max = 1000 if wait_random_max is None else wait_random_max
        self._wait_incrementing_start = 0 if wait_incrementing_start is None else wait_incrementing_start
        self._wait_incrementing_increment = 100 if wait_incrementing_increment is None else wait_incrementing_increment
        self._wait_exponential_multiplier = 1 if wait_exponential_multiplier is None else wait_exponential_multiplier
        self._wait_exponential_max = MAX_WAIT if wait_exponential_max is None else wait_exponential_max
        self._wait_incrementing_max = MAX_WAIT if wait_incrementing_max is None else wait_incrementing_max
        self._wait_jitter_max = 0 if wait_jitter_max is None else wait_jitter_max
        self._before_attempts = before_attempts
        self._after_attempts = after_attempts

        # Whether stop_after_delay participates in stopping; used to decide
        # if sleeps should be clamped to the remaining delay budget.
        self._has_delay_deadline = stop_max_delay is not None or stop == "stop_after_delay"

        # stop behavior
        stop_funcs: list[_StopFunc] = []
        if stop_max_attempt_number is not None:
            stop_funcs.append(self.stop_after_attempt)

        if stop_max_delay is not None:
            stop_funcs.append(self.stop_after_delay)

        if stop_func is not None:
            self.stop: _StopFunc = stop_func

        elif stop is None:
            self.stop = lambda attempts, delay: any(f(attempts, delay) for f in stop_funcs)

        else:
            self.stop = getattr(self, stop)

        # wait behavior
        wait_funcs: list[_WaitFunc] = [lambda *args, **kwargs: 0]
        if wait_fixed is not None:
            wait_funcs.append(self.fixed_sleep)

        if wait_random_min is not None or wait_random_max is not None:
            wait_funcs.append(self.random_sleep)

        if wait_incrementing_start is not None or wait_incrementing_increment is not None:
            wait_funcs.append(self.incrementing_sleep)

        if wait_exponential_multiplier is not None or wait_exponential_max is not None:
            wait_funcs.append(self.exponential_sleep)

        if wait_func is not None:
            self.wait: _WaitFunc = wait_func

        elif wait is None:
            self.wait = lambda attempts, delay: max(f(attempts, delay) for f in wait_funcs)

        else:
            self.wait = getattr(self, wait)

        # retry on exception filter
        if retry_on_exception is None:
            self._retry_on_exception: Callable[[BaseException], bool] = self.always_reject
        else:
            # allow a tuple of exception types instead of a callback
            if isinstance(retry_on_exception, tuple):
                retry_on_exception = _retry_if_exception_of_type(retry_on_exception)
            self._retry_on_exception = retry_on_exception

        # retry on result filter
        if retry_on_result is None:
            self._retry_on_result: Callable[[Any], bool] = self.never_reject
        else:
            self._retry_on_result = retry_on_result

        self._wrap_exception = wrap_exception

    def stop_after_attempt(self, previous_attempt_number: int, delay_since_first_attempt_ms: float) -> bool:
        """Stop after the previous attempt >= stop_max_attempt_number."""
        return previous_attempt_number >= self._stop_max_attempt_number

    def stop_after_delay(self, previous_attempt_number: int, delay_since_first_attempt_ms: float) -> bool:
        """Stop after the time from the first attempt >= stop_max_delay."""
        return delay_since_first_attempt_ms >= self._stop_max_delay

    @staticmethod
    def no_sleep(previous_attempt_number: int, delay_since_first_attempt_ms: float) -> float:
        """Don't sleep at all before retrying."""
        return 0

    def fixed_sleep(self, previous_attempt_number: int, delay_since_first_attempt_ms: float) -> float:
        """Sleep a fixed amount of time between each retry."""
        return self._wait_fixed

    def random_sleep(self, previous_attempt_number: int, delay_since_first_attempt_ms: float) -> float:
        """Sleep a random amount of time between wait_random_min and wait_random_max."""
        lo, hi = self._wait_random_min, self._wait_random_max
        if isinstance(lo, int) and isinstance(hi, int):
            # preserve historical integer-millisecond behavior exactly
            return random.randint(lo, hi)
        return random.uniform(lo, hi)

    def incrementing_sleep(self, previous_attempt_number: int, delay_since_first_attempt_ms: float) -> float:
        """
        Sleep an incremental amount of time after each attempt, starting at
        wait_incrementing_start and incrementing by wait_incrementing_increment.
        """
        result = self._wait_incrementing_start + (
            self._wait_incrementing_increment * (previous_attempt_number - 1)
        )
        if result > self._wait_incrementing_max:
            result = self._wait_incrementing_max
        if result < 0:
            result = 0
        return result

    def exponential_sleep(self, previous_attempt_number: int, delay_since_first_attempt_ms: float) -> float:
        exp = 2 ** previous_attempt_number
        result = self._wait_exponential_multiplier * exp
        if result > self._wait_exponential_max:
            result = self._wait_exponential_max
        if result < 0:
            result = 0
        return result

    @staticmethod
    def never_reject(result: Any) -> bool:
        return False

    @staticmethod
    def always_reject(result: Any) -> bool:
        return True

    def should_reject(self, attempt: "Attempt") -> bool:
        reject = False
        if attempt.has_exception:
            reject |= bool(self._retry_on_exception(attempt.value[1]))
        else:
            reject |= bool(self._retry_on_result(attempt.value))
        return reject

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        start_time = time.monotonic()
        attempt_number = 1
        while True:
            if self._before_attempts:
                self._before_attempts(attempt_number)

            try:
                attempt = Attempt(fn(*args, **kwargs), attempt_number, False)
            except Exception as exc:
                # NOTE: deliberately `except Exception`, not bare `except:`.
                # KeyboardInterrupt / SystemExit must propagate immediately
                # instead of being retried (historical bug).
                attempt = Attempt((type(exc), exc, exc.__traceback__), attempt_number, True)

            if not self.should_reject(attempt):
                return attempt.get(self._wrap_exception)

            if self._after_attempts:
                self._after_attempts(attempt_number)

            delay_since_first_attempt_ms = (time.monotonic() - start_time) * 1000
            if self.stop(attempt_number, delay_since_first_attempt_ms):
                if not self._wrap_exception and attempt.has_exception:
                    raise attempt.get()
                else:
                    raise RetryError(attempt)
            else:
                sleep = self.wait(attempt_number, delay_since_first_attempt_ms)
                if self._wait_jitter_max:
                    jitter = random.random() * self._wait_jitter_max
                    sleep = sleep + max(0, jitter)
                if self._has_delay_deadline:
                    # never sleep past the stop_max_delay deadline
                    remaining_ms = self._stop_max_delay - delay_since_first_attempt_ms
                    sleep = max(0, min(sleep, remaining_ms))
                # break traceback reference cycles for the discarded attempt
                del attempt
                time.sleep(sleep / 1000.0)

            attempt_number += 1


class Attempt:
    """
    An Attempt encapsulates a call to a target function that may end as a
    normal return value from the function or an Exception depending on what
    occurred during the execution.
    """

    def __init__(self, value: Any, attempt_number: int, has_exception: bool) -> None:
        self.value = value
        self.attempt_number = attempt_number
        self.has_exception = has_exception

    def get(self, wrap_exception: bool = False) -> Any:
        """
        Return the return value of this Attempt instance or raise an Exception.
        If wrap_exception is true, this Attempt is wrapped inside of a
        RetryError before being raised.
        """
        if self.has_exception:
            if wrap_exception:
                raise RetryError(self)
            else:
                exc = self.value[1]
                raise exc.with_traceback(self.value[2])
        else:
            return self.value

    def __repr__(self) -> str:
        if self.has_exception:
            return "Attempts: {0}, Error:\n{1}".format(
                self.attempt_number, "".join(traceback.format_tb(self.value[2]))
            )
        else:
            return "Attempts: {0}, Value: {1}".format(self.attempt_number, self.value)


class RetryError(Exception):
    """
    A RetryError encapsulates the last Attempt instance right before giving up.
    """

    def __init__(self, last_attempt: "Attempt") -> None:
        self.last_attempt = last_attempt

    def __str__(self) -> str:
        return "RetryError[{0}]".format(self.last_attempt)
