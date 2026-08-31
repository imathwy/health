"""User-facing errors shared by the local HealthLog application."""


class PipelineError(RuntimeError):
    """An expected pipeline failure that the CLI can report without a traceback."""
