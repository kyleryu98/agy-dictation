"""Bounded recovery for engine initialization, never recording or text insertion."""

import logging

CLI_READY_TIMEOUT = 45
# Allow the provider's deadline, child cleanup and IPC reply to finish first.
START_REQUEST_TIMEOUT = CLI_READY_TIMEOUT + 15
RETRY_DELAYS = (5, 15)
TRANSIENT_ERRORS = frozenset({
    "startup_timeout", "engine_unavailable", "cli_start_failed", "cli_exited", "busy",
})


def start_backend(backend, stopping, report):
    """Return False if stopping; retry only known transient initialization errors."""
    for attempt in range(len(RETRY_DELAYS) + 1):
        if stopping.is_set():
            return False
        try:
            backend.start()
            return not stopping.is_set()
        except Exception as error:
            backend.close()
            code = getattr(error, "code", None)
            if code not in TRANSIENT_ERRORS or attempt == len(RETRY_DELAYS):
                raise
            delay = RETRY_DELAYS[attempt]
            logging.warning("engine startup retry attempt=%s code=%s", attempt + 1, code)
            report("starting", f"음성 엔진 준비가 늦어져 {delay}초 후 다시 시도해요")
            if stopping.wait(delay):
                return False
    return False
