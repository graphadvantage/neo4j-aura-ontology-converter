"""Live notebook stage indicator with elapsed time and an idle-stage heartbeat."""
from html import escape
from threading import Event, Lock, Thread
from time import monotonic


class NotebookProgress:
    """Context manager and stage callback; no parser-completion estimates."""
    total = 6

    def __init__(self, total=6):
        self.total = total
        self._stop = Event()
        self._lock = Lock()
        self._stage = 0
        self._message = 'Starting'
        self._status = 'Running'
        self._handle = None

    def __enter__(self):
        self._started = monotonic()
        try:
            from IPython import get_ipython
            if get_ipython() is not None:
                from IPython.display import HTML, display
                self._html = HTML
                self._handle = display(HTML(self._markup()), display_id=True)
        except ImportError:
            pass
        self._thread = Thread(target=self._heartbeat, daemon=True)
        self._thread.start()
        return self

    def __call__(self, stage, message):
        with self._lock:
            self._stage = stage
            self._message = message
        self._render()

    def _markup(self):
        return (f'<div role="status"><strong>{escape(self._status)}</strong> · '
                f'Stage {self._stage}/{self.total} · {monotonic() - self._started:.1f}s elapsed<br>'
                f'<progress max="{self.total}" value="{self._stage}" style="width:100%;max-width:600px"></progress><br>'
                f'{escape(self._message)}</div>')

    def _render(self):
        with self._lock:
            if self._handle is not None:
                self._handle.update(self._html(self._markup()))
            else:
                print(f'\r{self._status} · Stage {self._stage}/{self.total} · '
                      f'{monotonic() - self._started:.1f}s · {self._message}', end='', flush=True)

    def _heartbeat(self):
        while not self._stop.wait(1):
            self._render()

    def __exit__(self, kind, error, traceback):
        self._stop.set()
        self._thread.join()
        with self._lock:
            self._status = 'Complete' if kind is None else ('Interrupted' if kind is KeyboardInterrupt else 'Failed')
            if error is not None:
                self._message = str(error) or self._status
        self._render()
        if self._handle is None:
            print()
        return False
