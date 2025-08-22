from enum import Enum

class UserRole(str, Enum):
    ADMIN = "admin"
    MAINTAINER = "maintainer"
    VIEWER = "viewer"

class StagingStatus(str, Enum):
    RUNNING = "running"
    FAILED = "failed"
    SUCCEEDED = "succeeded"

class InitiatedVia(str, Enum):
    MANUAL = "manual"
    CRON = "cron"