"""Normalized provider exception boundary consumed by structured invocation."""

from domain.model_runtime import ProviderError


class ProviderInvocationError(Exception):
    """Carry one safe normalized provider error without raw transport details."""

    def __init__(self, error: ProviderError) -> None:
        super().__init__(error.message)
        self.error = error
