class AutomationError(RuntimeError):
    """Base error for expected automation failures."""


class ManualReviewRequired(AutomationError):
    """The system found ambiguity or insufficient evidence and refuses to guess."""


class VerificationError(AutomationError):
    """A Fakturama value did not match the source/order model."""


class ExtractionError(AutomationError):
    """The source image could not be converted into a valid structured order."""
