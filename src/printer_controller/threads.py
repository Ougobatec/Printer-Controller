"""The only threading helpers of the app.

* `UiDispatcher`: worker threads call ``post(fn, ...)``; the Tk thread runs it.
* `run_in_background`: run a function in a thread, then call back on the Tk thread.
"""

import queue
import threading


class UiDispatcher:
    def __init__(self, root=None, interval_ms=30):
        self._queue = queue.Queue()
        self._root = root
        self._interval = interval_ms
        if root is not None:
            root.after(interval_ms, self._tick)

    def post(self, fn, *args, **kwargs):
        """Thread-safe: `fn(*args, **kwargs)` will run on the Tk thread."""
        self._queue.put((fn, args, kwargs))

    def drain(self):
        """Run everything that is waiting (called by the Tk timer; also used by tests)."""
        while True:
            try:
                fn, args, kwargs = self._queue.get_nowait()
            except queue.Empty:
                return
            fn(*args, **kwargs)

    def _tick(self):
        self.drain()
        self._root.after(self._interval, self._tick)


def run_in_background(dispatcher, work, on_done=None, on_error=None):
    """Run `work()` in a thread; `on_done(result)` or `on_error(exception)` run on the Tk thread."""

    def target():
        try:
            result = work()
        except Exception as exc:                          # reported to the caller, not lost
            if on_error:
                dispatcher.post(on_error, exc)
            return
        if on_done:
            dispatcher.post(on_done, result)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread
