"""Preview pipeline - run camera ops off the GTK main thread."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from grawji.core import CameraSession
from grawji.recipe import Recipe

Dispatch = Callable[[Callable[[], None]], Any]
OnDone = Callable[[Any], None]
OnError = Callable[[Exception], None]

_Job = tuple["Callable[[], Any]", "OnDone | None", "OnError | None", bool]


def _call_now(callback: Callable[[], None]) -> None:
    """Default dispatch: run the callback inline (tests / headless)."""
    callback()


class CameraWorker:
    """Runs ~grawji.core.CameraSession ops on a worker thread.

    Submit open / render from the GTK main thread; the work runs
    on a background thread and the on_done / on_error callbacks
    are handed back through dispatch. A render queued behind another
    not-yet-started render replaces it (coalescing), so rapid requests do
    not pile up; opens always run.

    Start the worker (or use it as a context manager) before submitting.
    """

    def __init__(
        self,
        session: CameraSession,
        *,
        dispatch: Dispatch = _call_now,
    ) -> None:
        """Create a worker.

        Args:
            session: The camera session to drive.
            dispatch: Schedules a callback on the UI thread. Defaults to
                running it inline; the GTK layer passes GLib.idle_add.
        """
        self._session = session
        self._dispatch = dispatch
        self._cond = threading.Condition()
        self._queue: deque[_Job] = deque()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background worker thread (idempotent)."""
        if self._thread is not None:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="grawji-camera", daemon=True
        )
        self._thread.start()

    def open(
        self,
        raf_path: str,
        *,
        on_done: OnDone | None = None,
        on_error: OnError | None = None,
    ) -> None:
        """Queue opening a RAF (connect + upload + read profile)."""
        self._submit(
            lambda: self._session.open(raf_path),
            on_done,
            on_error,
            coalesce=False,
        )

    def render(
        self,
        recipe: Recipe,
        *,
        full_resolution: bool,
        on_done: OnDone | None = None,
        on_error: OnError | None = None,
    ) -> None:
        """Queue a render; coalesces with a not-yet-started render."""
        self._submit(
            lambda: self._session.render(
                recipe, full_resolution=full_resolution
            ),
            on_done,
            on_error,
            coalesce=True,
        )

    def submit(
        self,
        task: Callable[[], Any],
        *,
        on_done: OnDone | None = None,
        on_error: OnError | None = None,
    ) -> None:
        """Queue an arbitrary camera task (not coalesced).

        For long sequential jobs (e.g. batch export) that drive the
        session directly. Runs on the worker thread, serialised with all
        other camera operations.
        """
        self._submit(task, on_done, on_error, coalesce=False)

    def stop(self, *, close_session: bool = True) -> None:
        """Stop the worker thread and (by default) close the session."""
        with self._cond:
            self._running = False
            self._cond.notify()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join()
        if close_session:
            self._session.close()

    def _submit(
        self,
        task: Callable[[], Any],
        on_done: OnDone | None,
        on_error: OnError | None,
        *,
        coalesce: bool,
    ) -> None:
        """Queue a job, coalescing a trailing render when asked."""
        with self._cond:
            # A new render replaces a trailing render that has not yet
            # started - the latest recipe is the only one worth rendering.
            if coalesce and self._queue and self._queue[-1][3]:
                self._queue[-1] = (task, on_done, on_error, coalesce)
            else:
                self._queue.append((task, on_done, on_error, coalesce))
            self._cond.notify()

    def _loop(self) -> None:
        """Process queued jobs on the worker thread until stopped."""
        while True:
            with self._cond:
                while not self._queue and self._running:
                    self._cond.wait()
                if not self._running:
                    return
                job = self._queue.popleft()
            self._run(job)

    def _run(self, job: _Job) -> None:
        """Run one job and dispatch its result or error."""
        task, on_done, on_error, _ = job
        try:
            result = task()
        except Exception as exc:  # delivered to on_error, not swallowed
            if on_error is not None:
                # Bind to a local that outlives the except scope (Python
                # deletes exc at block end) so the deferred lambda is safe.
                on_err, error = on_error, exc
                self._dispatch(lambda: on_err(error))
            return
        if on_done is not None:
            on_ok = on_done
            self._dispatch(lambda: on_ok(result))

    def __enter__(self) -> CameraWorker:
        """Start the worker on context entry."""
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Stop the worker (and close the session) on context exit."""
        self.stop()
