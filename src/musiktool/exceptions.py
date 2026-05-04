"""Custom exceptions for musiktool.

These replace assert-based validation so that errors produce
friendly messages instead of raw AssertionError tracebacks.
"""

from pathlib import Path


class MusiktoolError(Exception):
    """Base class for all musiktool errors."""


class ValidationError(MusiktoolError):
    """User input or configuration error (bad flags, missing data, etc.)."""


class ProjectNotFoundError(ValidationError):
    """Raised when a requested tape project does not exist."""


class MissingLoudnessError(ValidationError):
    """Raised when loudness data is required but not present in the DB."""


class InvalidMediumError(ValidationError):
    """Unknown medium preset or invalid parameters for custom medium."""


class PathNotFoundError(ValidationError):
    """A filesystem path required by the operation does not exist."""


class InvalidPositionError(ValidationError):
    """Position argument is out of range for the current project items."""


class AudioReadError(MusiktoolError):
    """Failed to read audio file (duration, sample rate, etc.)."""
