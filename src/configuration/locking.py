"""Writer-preferring synchronous shared read access."""

from collections.abc import Generator
from contextlib import contextmanager
from threading import Condition


class ReadWriteLock:
    def __init__(self) -> None:
        self._condition: Condition = Condition()
        self._reader_count: int = 0
        self._writer_active: bool = False
        self._waiting_writers: int = 0

    @contextmanager
    def read_lock(self) -> Generator[None]:
        with self._condition:
            self._condition.wait_for(lambda: not self._writer_active and self._waiting_writers == 0)
            self._reader_count += 1
        try:
            yield
        finally:
            with self._condition:
                self._reader_count -= 1
                self._condition.notify_all()

    @contextmanager
    def write_lock(self) -> Generator[None]:
        with self._condition:
            self._waiting_writers += 1
            try:
                self._condition.wait_for(lambda: not self._writer_active and self._reader_count == 0)
                self._writer_active = True
            finally:
                self._waiting_writers -= 1
                self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._writer_active = False
                self._condition.notify_all()
