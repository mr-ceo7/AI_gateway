"""
Configuration management for AI Gateway.
Uses environment variables with sensible defaults.
"""
import os
from dataclasses import dataclass
from typing import List, Set


@dataclass
class Config:
    """Application configuration."""
    
    # Server Configuration
    HOST: str = os.getenv('HOST', '0.0.0.0')
    PORT: int = int(os.getenv('PORT', '5000'))
    DEBUG: bool = os.getenv('DEBUG', 'False').lower() == 'true'
    SECRET_KEY: str = os.getenv('SECRET_KEY', os.urandom(32).hex())
    
    # File Upload Configuration
    UPLOAD_DIR: str = os.getenv(
        'UPLOAD_DIR',
        os.path.join(os.path.expanduser('~'), '.gemini_uploads')
    )
    MAX_FILE_SIZE: int = int(os.getenv('MAX_FILE_SIZE', str(50 * 1024 * 1024)))  # 50MB
    ALLOWED_EXTENSIONS: Set[str] = set(
        os.getenv('ALLOWED_EXTENSIONS', '.txt,.pdf,.md,.csv,.json').split(',')
    )
    ALLOWED_MIME_TYPES: Set[str] = {
        'text/plain',
        'application/pdf',
        'text/markdown',
        'text/csv',
        'application/json'
    }
    FILE_CLEANUP_INTERVAL: int = int(os.getenv('FILE_CLEANUP_INTERVAL', '3600'))  # 1 hour
    
    # Gemini CLI Configuration
    GEMINI_TIMEOUT: int = int(os.getenv('GEMINI_TIMEOUT', '300'))  # 5 minutes
    GEMINI_MAX_RETRIES: int = int(os.getenv('GEMINI_MAX_RETRIES', '3'))
    GEMINI_MAX_PROMPT_LENGTH: int = int(os.getenv('GEMINI_MAX_PROMPT_LENGTH', '100000'))  # 100KB
    
    # CORS Configuration
    CORS_ORIGINS: List[str] = os.getenv(
        'CORS_ORIGINS',
        'http://localhost:3000,http://localhost:3001,http://192.168.1.192:3000'
    ).split(',')
    
    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = os.getenv('RATE_LIMIT_ENABLED', 'True').lower() == 'true'
    RATE_LIMIT_STORAGE: str = os.getenv('RATE_LIMIT_STORAGE', 'memory://')  # or 'redis://localhost:6379/0'
    RATE_LIMIT_DEFAULT: str = os.getenv('RATE_LIMIT_DEFAULT', '100 per hour, 10 per minute')
    RATE_LIMIT_UPLOAD: str = os.getenv('RATE_LIMIT_UPLOAD', '5 per minute')
    RATE_LIMIT_GENERATE: str = os.getenv('RATE_LIMIT_GENERATE', '20 per minute')
    
    # Database Configuration
    DATABASE_URL: str = os.getenv('DATABASE_URL', 'sqlite:///ai_gateway.db')
    
    # Redis Configuration (for caching, sessions, rate limiting)
    REDIS_URL: str = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    REDIS_ENABLED: bool = os.getenv('REDIS_ENABLED', 'False').lower() == 'true'
    
    # Logging Configuration
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE: str = os.getenv('LOG_FILE', 'logs/app.log')
    LOG_MAX_BYTES: int = int(os.getenv('LOG_MAX_BYTES', str(10 * 1024 * 1024)))  # 10MB
    LOG_BACKUP_COUNT: int = int(os.getenv('LOG_BACKUP_COUNT', '5'))
    
    # Session Configuration
    SESSION_LIFETIME: int = int(os.getenv('SESSION_LIFETIME', '86400'))  # 24 hours
    SESSION_TYPE: str = os.getenv('SESSION_TYPE', 'filesystem')  # or 'redis'
    
    # Monitoring
    METRICS_ENABLED: bool = os.getenv('METRICS_ENABLED', 'True').lower() == 'true'
    METRICS_PORT: int = int(os.getenv('METRICS_PORT', '9090'))
    
    # Feature Flags
    ENABLE_CACHING: bool = os.getenv('ENABLE_CACHING', 'False').lower() == 'true'
    ENABLE_ASYNC_PROCESSING: bool = os.getenv('ENABLE_ASYNC_PROCESSING', 'False').lower() == 'true'
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        # Ensure upload directory exists
        os.makedirs(self.UPLOAD_DIR, exist_ok=True)
        
        # Ensure log directory exists
        log_dir = os.path.dirname(self.LOG_FILE)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)


# Global config instance
config = Config()

