from __future__ import annotations


class AcquisitionError(RuntimeError):
    """Configuration or provider failure during data acquisition."""


class MissingTokenError(AcquisitionError):
    """APIFY_API_TOKEN (or equivalent) is not set."""
