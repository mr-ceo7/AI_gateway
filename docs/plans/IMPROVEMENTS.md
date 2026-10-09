# AI Gateway - Stability, Robustness & Scalability Improvements

## Table of Contents
1. [Security Improvements](#security-improvements)
2. [Error Handling & Robustness](#error-handling--robustness)
3. [Scalability Improvements](#scalability-improvements)
4. [Performance Optimizations](#performance-optimizations)
5. [Code Quality & Architecture](#code-quality--architecture)
6. [Monitoring & Observability](#monitoring--observability)
7. [Configuration Management](#configuration-management)
8. [Testing Strategy](#testing-strategy)

---

## Security Improvements

### 1. File Upload Validation
**Current Issue**: Limited validation on file uploads, potential path traversal, no size limits.

**Improvements**:
```python
# Add to app.py
import magic  # python-magic for file type detection
from pathlib import Path

# Configuration
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_EXTENSIONS = {'.txt', '.pdf', '.md', '.csv', '.json'}
ALLOWED_MIME_TYPES = {
    'text/plain', 'application/pdf', 'text/markdown',
    'text/csv', 'application/json'
}

def validate_file(file_content, filename):
    """Comprehensive file validation."""
    errors = []
    
    # Size check
    if len(file_content) > MAX_FILE_SIZE:
        errors.append(f"File exceeds maximum size of {MAX_FILE_SIZE / 1024 / 1024}MB")
    
    # Extension check
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        errors.append(f"File extension {ext} not allowed")
    
    # MIME type check (more secure than extension)
    try:
        mime = magic.Magic(mime=True)
        detected_mime = mime.from_buffer(file_content[:1024])
        if detected_mime not in ALLOWED_MIME_TYPES:
            errors.append(f"File type {detected_mime} not allowed")
    except Exception as e:
        app.logger.warning(f"MIME detection failed: {e}")
    
    # Path traversal protection
    if '..' in filename or '/' in filename or '\\' in filename:
        errors.append("Invalid filename: path traversal detected")
    
    return errors

# Update upload endpoint
@app.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        # ... existing code ...
        
        # Validate file
        validation_errors = validate_file(file_content, filename)
        if validation_errors:
            return jsonify({'error': 'Validation failed', 'details': validation_errors}), 400
        
        # ... rest of code ...
```

### 2. Rate Limiting
**Current Issue**: No rate limiting, vulnerable to abuse.

**Improvement**:
```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["100 per hour", "10 per minute"],
    storage_uri="redis://localhost:6379"  # Or use memory for simple setup
)

@app.route('/api/upload', methods=['POST'])
@limiter.limit("5 per minute")  # Stricter limit for uploads
def upload_file():
    # ... existing code ...

@app.route('/api/generate', methods=['POST'])
@limiter.limit("20 per minute")
def generate():
    # ... existing code ...
```

### 3. Input Sanitization
**Current Issue**: User input directly passed to subprocess without sanitization.

**Improvement**:
```python
import shlex

def sanitize_prompt(prompt):
    """Sanitize prompt to prevent command injection."""
    # Remove potentially dangerous characters
    prompt = prompt.replace('\x00', '')  # Null bytes
    prompt = prompt.replace('\r', '')     # Carriage returns
    # Limit length
    if len(prompt) > 100000:  # 100KB limit
        raise ValueError("Prompt too long")
    return prompt

# In generate() function:
prompt = sanitize_prompt(prompt)
```

### 4. Secure Session Management
**Current Issue**: Global state for sessions, no proper session management.

**Improvement**:
```python
from flask_session import Session
import secrets

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_TYPE'] = 'redis'  # Or 'filesystem' for simpler setup
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
Session(app)
```

---

## Error Handling & Robustness

### 1. Comprehensive Error Handling
**Current Issue**: Generic exception catching, no structured error responses.

**Improvement**:
```python
from enum import Enum
from functools import wraps

class ErrorCode(Enum):
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE"
    GEMINI_CLI_ERROR = "GEMINI_CLI_ERROR"
    AUTH_FAILED = "AUTH_FAILED"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"

class APIError(Exception):
    def __init__(self, message, code, status_code=500):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(self.message)

@app.errorhandler(APIError)
def handle_api_error(error):
    return jsonify({
        'error': error.message,
        'code': error.code.value,
        'timestamp': time.time()
    }), error.status_code

@app.errorhandler(Exception)
def handle_generic_error(e):
    app.logger.exception("Unhandled exception")
    return jsonify({
        'error': 'Internal server error',
        'code': ErrorCode.INTERNAL_ERROR.value,
        'timestamp': time.time()
    }), 500

# Usage in endpoints
@app.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        # ... validation ...
        if len(file_content) > MAX_FILE_SIZE:
            raise APIError(
                f"File exceeds maximum size",
                ErrorCode.FILE_TOO_LARGE,
                400
            )
        # ... rest of code ...
    except APIError:
        raise  # Re-raise API errors
    except Exception as e:
        app.logger.exception("Upload failed")
        raise APIError(
            "File upload failed",
            ErrorCode.INTERNAL_ERROR,
            500
        )
```

### 2. Retry Logic with Exponential Backoff
**Current Issue**: No retry mechanism for transient failures.

**Improvement**:
```python
import backoff

@backoff.on_exception(
    backoff.expo,
    (subprocess.TimeoutExpired, ConnectionError),
    max_tries=3,
    max_time=30
)
def run_gemini_with_retry(prompt, stream=False):
    """Run Gemini CLI with automatic retry on failure."""
    try:
        # ... existing subprocess code ...
    except subprocess.TimeoutExpired:
        app.logger.warning("Gemini CLI timeout, retrying...")
        raise
    except Exception as e:
        app.logger.error(f"Gemini CLI error: {e}")
        raise
```

### 3. Process Management & Cleanup
**Current Issue**: Subprocesses may not be properly cleaned up.

**Improvement**:
```python
import signal
import atexit
from contextlib import contextmanager

active_processes = set()

@contextmanager
def managed_process(cmd, **kwargs):
    """Context manager for subprocess with guaranteed cleanup."""
    process = None
    try:
        process = subprocess.Popen(cmd, **kwargs)
        active_processes.add(process)
        yield process
    finally:
        if process:
            active_processes.discard(process)
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
            except Exception as e:
                app.logger.error(f"Error cleaning up process: {e}")

def cleanup_all_processes():
    """Cleanup all active processes on shutdown."""
    for process in list(active_processes):
        try:
            if process.poll() is None:
                process.terminate()
        except Exception:
            pass

atexit.register(cleanup_all_processes)
signal.signal(signal.SIGTERM, lambda s, f: cleanup_all_processes())
```

### 4. File Locking for Concurrent Access
**Current Issue**: Race conditions in file operations.

**Improvement**:
```python
import fcntl
from contextlib import contextmanager

@contextmanager
def file_lock(filepath):
    """File lock for concurrent access protection."""
    lock_file = f"{filepath}.lock"
    with open(lock_file, 'w') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            try:
                os.remove(lock_file)
            except:
                pass

# Usage
with file_lock(file_path):
    with open(file_path, 'wb') as f:
        f.write(file_content)
```

---

## Scalability Improvements

### 1. Database for Session & File Management
**Current Issue**: In-memory state, no persistence, doesn't scale.

**Improvement**:
```python
# Use SQLite for simple setup, PostgreSQL for production
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

db = SQLAlchemy(app)

class UploadedFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), unique=True, nullable=False)
    original_name = db.Column(db.String(255))
    session_id = db.Column(db.String(64), index=True)
    size = db.Column(db.Integer)
    context_mode = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)
    
    def is_expired(self):
        return self.expires_at and datetime.utcnow() > self.expires_at

class Session(db.Model):
    id = db.Column(db.String(64), primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_activity = db.Column(db.DateTime, default=datetime.utcnow)
    files = db.relationship('UploadedFile', backref='session', lazy=True)

# Cleanup expired files
def cleanup_expired_files():
    """Background task to clean expired files."""
    expired = UploadedFile.query.filter(
        UploadedFile.expires_at < datetime.utcnow()
    ).all()
    for file in expired:
        try:
            filepath = os.path.join(UPLOAD_DIR, file.filename)
            if os.path.exists(filepath):
                os.remove(filepath)
            db.session.delete(file)
        except Exception as e:
            app.logger.error(f"Error deleting expired file {file.filename}: {e}")
    db.session.commit()

# Run cleanup periodically
import threading
def periodic_cleanup():
    while True:
        time.sleep(3600)  # Every hour
        cleanup_expired_files()

threading.Thread(target=periodic_cleanup, daemon=True).start()
```

### 2. Message Queue for Async Processing
**Current Issue**: Synchronous processing blocks requests.

**Improvement**:
```python
# Using Celery or RQ for background tasks
from celery import Celery

celery_app = Celery(
    'ai_gateway',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/0'
)

@celery_app.task(bind=True, max_retries=3)
def process_gemini_request(self, prompt, files, session_id):
    """Async task for Gemini processing."""
    try:
        # ... process request ...
        return result
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

@app.route('/api/generate', methods=['POST'])
def generate():
    data = request.get_json()
    # ... validation ...
    
    # Queue async task
    task = process_gemini_request.delay(prompt, files, session_id)
    return jsonify({
        'task_id': task.id,
        'status': 'processing',
        'status_url': f'/api/tasks/{task.id}'
    }), 202  # Accepted
```

### 3. Caching Layer
**Current Issue**: No caching, repeated queries hit API.

**Improvement**:
```python
from flask_caching import Cache
import hashlib

cache = Cache(app, config={
    'CACHE_TYPE': 'redis',
    'CACHE_REDIS_URL': 'redis://localhost:6379/1'
})

def get_cache_key(prompt, files):
    """Generate cache key from prompt and files."""
    content = f"{prompt}:{':'.join(sorted(files))}"
    return f"gemini:{hashlib.sha256(content.encode()).hexdigest()}"

@app.route('/api/generate', methods=['POST'])
def generate():
    # ... validation ...
    
    # Check cache
    cache_key = get_cache_key(prompt, files)
    cached = cache.get(cache_key)
    if cached:
        return jsonify({'response': cached, 'cached': True})
    
    # Process request
    result = process_gemini_request(prompt, files)
    
    # Cache result (TTL: 1 hour)
    cache.set(cache_key, result, timeout=3600)
    
    return jsonify({'response': result, 'cached': False})
```

### 4. Load Balancing & Horizontal Scaling
**Current Issue**: Single instance, no horizontal scaling.

**Improvement**:
- Use shared Redis for session storage
- Use shared file storage (S3, NFS, or object storage)
- Stateless application design
- Health check endpoint for load balancer

```python
@app.route('/health')
def health_check():
    """Health check endpoint for load balancer."""
    checks = {
        'status': 'healthy',
        'timestamp': time.time(),
        'checks': {}
    }
    
    # Check upload directory
    try:
        os.access(UPLOAD_DIR, os.W_OK)
        checks['checks']['upload_dir'] = 'ok'
    except:
        checks['checks']['upload_dir'] = 'error'
        checks['status'] = 'unhealthy'
    
    # Check Gemini CLI
    try:
        result = subprocess.run(
            ['gemini', '--version'],
            capture_output=True,
            timeout=5
        )
        checks['checks']['gemini_cli'] = 'ok' if result.returncode == 0 else 'error'
    except:
        checks['checks']['gemini_cli'] = 'error'
        checks['status'] = 'unhealthy'
    
    status_code = 200 if checks['status'] == 'healthy' else 503
    return jsonify(checks), status_code
```

---

## Performance Optimizations

### 1. Async File Operations
**Current Issue**: Blocking I/O operations.

**Improvement**:
```python
import asyncio
from aiofiles import open as aio_open
import aiofiles.os

async def async_save_file(file_path, content):
    """Async file save."""
    async with aio_open(file_path, 'wb') as f:
        await f.write(content)

# Use in upload endpoint with async Flask
from quart import Quart  # Or use Flask with async support
```

### 2. Connection Pooling
**Current Issue**: New subprocess for each request.

**Improvement**:
```python
from concurrent.futures import ThreadPoolExecutor
import queue

class GeminiProcessPool:
    def __init__(self, pool_size=3):
        self.pool_size = pool_size
        self.pool = queue.Queue(maxsize=pool_size)
        self.executor = ThreadPoolExecutor(max_workers=pool_size)
    
    def get_process(self):
        """Get or create a process from pool."""
        try:
            return self.pool.get_nowait()
        except queue.Empty:
            # Create new process
            return self._create_process()
    
    def return_process(self, process):
        """Return process to pool."""
        if process.poll() is None:  # Still alive
            try:
                self.pool.put_nowait(process)
            except queue.Full:
                process.terminate()
        else:
            # Process died, create new one
            pass

gemini_pool = GeminiProcessPool()
```

### 3. Streaming Optimization
**Current Issue**: Inefficient streaming implementation.

**Improvement**:
```python
def generate_output():
    try:
        process = gemini_pool.get_process()
        try:
            # ... streaming logic ...
            yield data
        finally:
            gemini_pool.return_process(process)
    except Exception as e:
        app.logger.exception("Streaming error")
        yield f"data: [ERROR] {str(e)}\n\n"
```

---

## Code Quality & Architecture

### 1. Configuration Management
**Current Issue**: Hardcoded values, no environment-based config.

**Improvement**:
```python
# config.py
import os
from dataclasses import dataclass

@dataclass
class Config:
    # Server
    HOST: str = os.getenv('HOST', '0.0.0.0')
    PORT: int = int(os.getenv('PORT', '5000'))
    DEBUG: bool = os.getenv('DEBUG', 'False').lower() == 'true'
    
    # File Upload
    UPLOAD_DIR: str = os.getenv('UPLOAD_DIR', os.path.join(os.path.expanduser('~'), '.gemini_uploads'))
    MAX_FILE_SIZE: int = int(os.getenv('MAX_FILE_SIZE', str(50 * 1024 * 1024)))
    ALLOWED_EXTENSIONS: set = set(os.getenv('ALLOWED_EXTENSIONS', '.txt,.pdf,.md').split(','))
    
    # Gemini
    GEMINI_TIMEOUT: int = int(os.getenv('GEMINI_TIMEOUT', '300'))
    GEMINI_MAX_RETRIES: int = int(os.getenv('GEMINI_MAX_RETRIES', '3'))
    
    # CORS
    CORS_ORIGINS: list = os.getenv('CORS_ORIGINS', 'http://localhost:3000').split(',')
    
    # Redis
    REDIS_URL: str = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    
    # Database
    DATABASE_URL: str = os.getenv('DATABASE_URL', 'sqlite:///ai_gateway.db')

config = Config()
```

### 2. Dependency Injection
**Current Issue**: Tight coupling, hard to test.

**Improvement**:
```python
# services/gemini_service.py
class GeminiService:
    def __init__(self, upload_dir, timeout=300):
        self.upload_dir = upload_dir
        self.timeout = timeout
    
    def generate(self, prompt, files, stream=False):
        # ... implementation ...
        pass

# In app.py
gemini_service = GeminiService(UPLOAD_DIR, config.GEMINI_TIMEOUT)

@app.route('/api/generate', methods=['POST'])
def generate():
    # ... validation ...
    result = gemini_service.generate(prompt, files, stream)
    return result
```

### 3. Logging Infrastructure
**Current Issue**: Print statements, no structured logging.

**Improvement**:
```python
import logging
from logging.handlers import RotatingFileHandler
import json

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            'timestamp': self.formatTime(record),
            'level': record.levelname,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno
        }
        if hasattr(record, 'request_id'):
            log_data['request_id'] = record.request_id
        return json.dumps(log_data)

# Setup logging
def setup_logging(app):
    if not app.debug:
        file_handler = RotatingFileHandler(
            'logs/app.log',
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5
        )
        file_handler.setFormatter(JSONFormatter())
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    app.logger.addHandler(console_handler)
    app.logger.setLevel(logging.INFO)

# Request ID middleware
@app.before_request
def before_request():
    request.request_id = secrets.token_hex(8)
    app.logger.info("Request started", extra={'request_id': request.request_id})
```

---

## Monitoring & Observability

### 1. Metrics Collection
**Current Issue**: No metrics, can't monitor performance.

**Improvement**:
```python
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from flask import Response

# Metrics
request_count = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint'])
request_duration = Histogram('http_request_duration_seconds', 'HTTP request duration')
active_uploads = Gauge('active_uploads', 'Number of active file uploads')
gemini_requests = Counter('gemini_requests_total', 'Total Gemini API requests')
gemini_errors = Counter('gemini_errors_total', 'Total Gemini API errors')

@app.before_request
def before_request():
    request.start_time = time.time()

@app.after_request
def after_request(response):
    duration = time.time() - request.start_time
    request_duration.observe(duration)
    request_count.labels(method=request.method, endpoint=request.endpoint).inc()
    return response

@app.route('/metrics')
def metrics():
    return Response(generate_latest(), mimetype='text/plain')
```

### 2. Distributed Tracing
**Current Issue**: No request tracing across services.

**Improvement**:
```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.jaeger import JaegerExporter

trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer(__name__)

@app.route('/api/generate', methods=['POST'])
def generate():
    with tracer.start_as_current_span("generate_request") as span:
        span.set_attribute("prompt_length", len(prompt))
        span.set_attribute("file_count", len(files))
        # ... process request ...
```

---

## Testing Strategy

### 1. Unit Tests
```python
# tests/test_upload.py
import pytest
from app import app, validate_file

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_upload_valid_file(client):
    data = {'file': (io.BytesIO(b'test content'), 'test.txt')}
    response = client.post('/api/upload', data=data, content_type='multipart/form-data')
    assert response.status_code == 200
    assert 'filename' in response.json

def test_upload_invalid_size(client):
    large_file = b'x' * (51 * 1024 * 1024)  # 51MB
    data = {'file': (io.BytesIO(large_file), 'large.txt')}
    response = client.post('/api/upload', data=data)
    assert response.status_code == 400
```

### 2. Integration Tests
```python
# tests/test_integration.py
def test_full_workflow(client):
    # Upload file
    upload_response = client.post('/api/upload', ...)
    filename = upload_response.json['filename']
    
    # Generate with file
    generate_response = client.post('/api/generate', json={
        'prompt': 'Summarize',
        'files': [filename]
    })
    assert generate_response.status_code == 200
    assert 'response' in generate_response.json
```

### 3. Load Testing
```python
# Use locust or pytest-benchmark
from locust import HttpUser, task, between

class AIGatewayUser(HttpUser):
    wait_time = between(1, 3)
    
    @task
    def upload_and_generate(self):
        # Upload file
        self.client.post('/api/upload', files={'file': ...})
        # Generate
        self.client.post('/api/generate', json={...})
```

---

## Implementation Priority

### Phase 1 (Critical - Immediate)
1. ✅ File upload validation & security
2. ✅ Error handling & structured responses
3. ✅ Rate limiting
4. ✅ Process cleanup & resource management
5. ✅ Configuration management

### Phase 2 (High Priority - Short Term)
1. ✅ Database for session management
2. ✅ Logging infrastructure
3. ✅ Health check endpoint
4. ✅ Retry logic
5. ✅ File locking

### Phase 3 (Medium Priority - Medium Term)
1. ✅ Caching layer
2. ✅ Metrics & monitoring
3. ✅ Async processing (message queue)
4. ✅ Connection pooling
5. ✅ Unit tests

### Phase 4 (Nice to Have - Long Term)
1. ✅ Distributed tracing
2. ✅ Load testing suite
3. ✅ Performance optimizations
4. ✅ Advanced monitoring dashboards

---

## Additional Recommendations

1. **API Versioning**: Add `/api/v1/` prefix for future compatibility
2. **Documentation**: Use OpenAPI/Swagger for API documentation
3. **CI/CD**: Set up automated testing and deployment
4. **Backup Strategy**: Regular backups of uploaded files and database
5. **Disaster Recovery**: Plan for service failures and data recovery
6. **Security Audits**: Regular security reviews and dependency updates
7. **Performance Budget**: Set and monitor performance targets
8. **Graceful Degradation**: Handle service unavailability gracefully

