"""Server-side diagnostics with no request data in exception traces."""
from contextvars import ContextVar
import logging
import traceback


request_id = ContextVar("dodo_request_id", default="-")


class DiagnosticFormatter(logging.Formatter):
    def __init__(self, debug):
        super().__init__("%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s")
        self.debug = debug

    def format(self, record):
        record.request_id = request_id.get()
        return super().format(record)

    def formatException(self, exc_info):
        kind, _, trace = exc_info
        frames = []
        if self.debug:
            for frame, line in traceback.walk_tb(trace):
                code = frame.f_code
                frames.append(f"  {code.co_filename}:{line} in {code.co_name}")
        # Exception messages can contain input values, URLs, or SQL parameters.
        frames.append(f"{kind.__module__}.{kind.__qualname__}")
        return "\n".join(frames)


def configure_logging(debug):
    handler = logging.StreamHandler()
    handler.setFormatter(DiagnosticFormatter(debug))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
    for name in ("dodo", "subsites"):
        logging.getLogger(name).setLevel(logging.DEBUG if debug else logging.INFO)
