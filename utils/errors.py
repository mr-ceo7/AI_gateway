"""
Error handling and custom exceptions.
"""
from enum import Enum
from typing import Optional
import time


class ErrorCode(Enum):
    """Error codes for API responses."""
    # File errors
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_UPLOAD_FAILED = "FILE_UPLOAD_FAILED"
    
    # Validation errors
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_INPUT = "INVALID_INPUT"
    
    # Gemini CLI errors
    GEMINI_CLI_ERROR = "GEMINI_CLI_ERROR"
    GEMINI_TIMEOUT = "GEMINI_TIMEOUT"
    GEMINI_AUTH_FAILED = "GEMINI_AUTH_FAILED"
    
    # System errors
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    
    # Session errors
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_EXPIRED = "SESSION_EXPIRED"


class APIError(Exception):
    """Base exception for API errors."""
    
    def __init__(
        self,
        message: str,
        code: ErrorCode,
        status_code: int = 500,
        details: Optional[dict] = None
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        self.timestamp = time.time()
        super().__init__(self.message)
    
    def to_dict(self) -> dict:
        """Convert error to dictionary for JSON response."""
        return {
            'error': self.message,
            'code': self.code.value,
            'status_code': self.status_code,
            'timestamp': self.timestamp,
            'details': self.details
        }


class ValidationError(APIError):
    """Raised when input validation fails."""
    
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(
            message,
            ErrorCode.VALIDATION_ERROR,
            400,
            details
        )


class FileError(APIError):
    """Raised when file operations fail."""
    
    def __init__(self, message: str, code: ErrorCode, details: Optional[dict] = None):
        super().__init__(message, code, 400, details)


class GeminiError(APIError):
    """Raised when Gemini CLI operations fail."""
    
    def __init__(self, message: str, code: ErrorCode, details: Optional[dict] = None):
        status_code = 503 if code == ErrorCode.GEMINI_TIMEOUT else 500
        super().__init__(message, code, status_code, details)

