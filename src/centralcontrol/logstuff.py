import sys
import logging

# for logging directly to systemd journal if we can
try:
    import systemd.journal
except ImportError:
    pass


def get_logger(logname=None, level=logging.DEBUG, always_terminal: bool = False) -> logging.Logger:
    """sets up logging"""
    lg = logging.getLogger(logname)
    lg.setLevel(level)

    def setup_term_logger(lg: logging.Logger) -> None:
        """sets up logging to the terminal"""
        # for logging to stdout & stderr
        ch = logging.StreamHandler()
        logFormat = logging.Formatter(("%(asctime)s|%(name)s|%(levelname)s|%(message)s"))
        ch.setFormatter(logFormat)
        lg.addHandler(ch)
        return None

    def setup_systemd_logger(lg: logging.Logger) -> None:
        sysdl = systemd.journal.JournalHandler(SYSLOG_IDENTIFIER=lg.name)
        sysLogFormat = logging.Formatter(("%(levelname)s|%(message)s"))
        sysdl.setFormatter(sysLogFormat)
        lg.addHandler(sysdl)
        if always_terminal:
            setup_term_logger(lg)

    if not lg.hasHandlers():
        # set up logging to systemd's journal if it's there
        if "systemd" in sys.modules:
            setup_systemd_logger(lg)
            if always_terminal:
                setup_term_logger(lg)
        else:
            setup_term_logger(lg)

    return lg
