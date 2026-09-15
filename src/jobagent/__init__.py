"""Job agent package; install minimal private diagnostics before app imports."""

from .privacy_logging import install_privacy_logging

install_privacy_logging()
