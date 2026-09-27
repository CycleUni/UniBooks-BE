"""Gunicorn settings, read automatically from the working directory.

Logging only. Gunicorn writes its own log ("error log", its name for the
server log) to stderr, and Railway marks every stderr line as an error,
so routine startup lines such as "Listening at: ..." showed up as errors.
Here INFO goes to stdout and WARNING and above stay on stderr, so a worker
timeout or crash is still flagged.

Setting logconfig_dict changes two other things, both put back below:
  - it turns on gunicorn's access log, which was off: one line per request;
  - gunicorn's defaults point the root logger at stdout from INFO, which
    would send the app's own logger.exception() output to stdout (so
    Railway would call it info) and start printing its INFO lines.
"""
import logging


class _BelowWarning(logging.Filter):
    def filter(self, record):
        return record.levelno < logging.WARNING


logconfig_dict = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'generic': {
            'format': '%(asctime)s [%(process)d] [%(levelname)s] %(message)s',
            'datefmt': '[%Y-%m-%d %H:%M:%S %z]',
        },
    },
    'filters': {
        'below_warning': {'()': _BelowWarning},
    },
    'handlers': {
        'stdout': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stdout',
            'formatter': 'generic',
            'filters': ['below_warning'],
        },
        'stderr': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stderr',
            'formatter': 'generic',
            'level': 'WARNING',
        },
    },
    'root': {'level': 'WARNING', 'handlers': ['stderr']},
    'loggers': {
        'gunicorn.error': {'level': 'INFO', 'handlers': ['stdout', 'stderr'], 'propagate': False},
        # Kept off, as it was before this file existed.
        'gunicorn.access': {'level': 'WARNING', 'handlers': [], 'propagate': False},
    },
}
