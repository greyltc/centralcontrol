import sys
import logging
import typing

# for logging directly to systemd journal if we can
try:
    import systemd.journal
except ImportError:
    pass


class NewHandler(logging.Handler):
    emitter: typing.Callable

    def __init__(self, emitter: typing.Callable, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.emitter = emitter

    def emit(self, record):
        # msg = self.format(record)
        self.emitter(record)


def get_logger(logname: str | None = None, level: int = logging.DEBUG, always_terminal: bool = False) -> logging.Logger:
    """sets up logging"""
    rl = logging.getLogger()  # get root logger
    rl.setLevel(logging.NOTSET)  # prevent root logger from filtering anything
    lg = logging.getLogger(logname)
    lg.setLevel(level)

    def setup_term_logger(lg: logging.Logger) -> None:
        """sets up logging to the terminal"""
        this_type = logging.StreamHandler
        if not any([isinstance(h, this_type) for h in lg.handlers]):
            # for logging to stdout & stderr
            ch = logging.StreamHandler()
            logFormat = logging.Formatter(("%(asctime)s|%(name)s|%(levelname)s|%(filename)s:%(lineno)d|%(funcName)s|%(message)s"))
            ch.setFormatter(logFormat)
            # ch.set_name(this_name)
            lg.addHandler(ch)

    def setup_systemd_logger(lg: logging.Logger) -> None:
        this_type = systemd.journal.JournalHandler
        if not any([isinstance(h, this_type) for h in lg.handlers]):
            sysdl = systemd.journal.JournalHandler(SYSLOG_IDENTIFIER=lg.name)
            sysLogFormat = logging.Formatter(("%(levelname)s|%(filename)s:%(lineno)d|%(funcName)s|%(message)s"))
            sysdl.setFormatter(sysLogFormat)
            # sysdl.set_name(this_name)
            lg.addHandler(sysdl)

    if "systemd" in sys.modules:
        setup_systemd_logger(lg)

    if (not lg.hasHandlers()) or always_terminal:
        setup_term_logger(lg)

    return lg
