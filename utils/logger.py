"""
Logger Utility
==============
Central logger factory so every module gets a consistently
formatted logger with a single call: get_logger(__name__)
"""

import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger for the given module name.
    Adds a StreamHandler on first call so logs appear in the console.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)

    logger.setLevel(logging.INFO)
    logger.propagate = False  
    return logger