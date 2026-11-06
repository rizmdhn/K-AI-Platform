"""Shared fixed-message storage errors; retained for native binding compatibility."""

class ApprovalError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def storage_error(error, messages):
    """Map the kai.* functions' SQLSTATEs to a status with a fixed, secret-free message."""
    status = {"K0001": 403, "K0002": 409, "K0003": 400}.get(error.sqlstate, 503)
    return ApprovalError(status, messages[status])

