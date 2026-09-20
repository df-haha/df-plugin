class CollaborationError(RuntimeError):
    """Base fail-closed collaboration error."""


class AuthorizationError(CollaborationError):
    pass


class StateConflictError(CollaborationError):
    pass


class ValidationError(CollaborationError):
    pass
