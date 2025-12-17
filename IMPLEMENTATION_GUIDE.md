# Implementation Guide for Improvements

This guide provides step-by-step instructions for implementing the improvements outlined in `IMPROVEMENTS.md`.

## Quick Start

### 1. Install New Dependencies

```bash
pip install -r requirements_improved.txt
```

### 2. Update app.py Structure

The improvements require refactoring `app.py`. Here's the recommended structure:

```python
# app.py - Updated structure
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import config
from utils.errors import APIError, ErrorCode, handle_api_error, handle_generic_error
from utils.validators import validate_file, sanitize_filename, sanitize_prompt, validate_file_list
from utils.process_manager import managed_process, setup_signal_handlers
from utils.logging_config import setup_logging

app = Flask(__name__)
app.config.from_object(config)

# Setup logging
setup_logging(app)

# Setup CORS
CORS(app, resources={
    r"/api/*": {
        "origins": config.CORS_ORIGINS,
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-Session-ID"]
    }
})

# Setup rate limiting
if config.RATE_LIMIT_ENABLED:
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=[config.RATE_LIMIT_DEFAULT],
        storage_uri=config.RATE_LIMIT_STORAGE
    )
else:
    limiter = None

# Error handlers
app.register_error_handler(APIError, handle_api_error)
app.register_error_handler(Exception, handle_generic_error)

# ... rest of your routes with updated error handling ...
```

### 3. Update Upload Endpoint

```python
@app.route('/api/upload', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_UPLOAD) if limiter else lambda f: f
def upload_file():
    """Handle file uploads with validation."""
    try:
        # Get file from request
        if request.files and 'file' in request.files:
            file = request.files['file']
            if file.filename == '':
                raise APIError('No file selected', ErrorCode.VALIDATION_ERROR, 400)
            
            filename = sanitize_filename(file.filename)
            file_content = file.read()
        elif request.is_json and 'file' in request.json:
            data = request.json
            filename = sanitize_filename(data.get('filename', 'uploaded_file'))
            file_content = base64.b64decode(data['file'])
        else:
            raise APIError('No file provided', ErrorCode.VALIDATION_ERROR, 400)
        
        # Validate file
        validation_errors = validate_file(file_content, filename)
        if validation_errors:
            raise APIError(
                'File validation failed',
                ErrorCode.INVALID_FILE_TYPE,
                400,
                {'errors': validation_errors}
            )
        
        # ... rest of upload logic ...
        
    except APIError:
        raise
    except Exception as e:
        app.logger.exception("Upload error")
        raise APIError('File upload failed', ErrorCode.FILE_UPLOAD_FAILED, 500)
```

### 4. Update Generate Endpoint

```python
@app.route('/api/generate', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_GENERATE) if limiter else lambda f: f
def generate():
    """Generate AI response with validation."""
    try:
        data = request.get_json()
        if not data:
            raise APIError('Missing request body', ErrorCode.VALIDATION_ERROR, 400)
        
        # Validate prompt
        if 'prompt' in data:
            prompt = sanitize_prompt(data['prompt'])
        elif 'messages' in data:
            # Build prompt from messages
            prompt = build_prompt_from_messages(data['messages'])
            prompt = sanitize_prompt(prompt)
        else:
            raise APIError('Missing prompt or messages', ErrorCode.VALIDATION_ERROR, 400)
        
        # Validate files if provided
        files = data.get('files', [])
        if files:
            file_errors = validate_file_list(files, UPLOAD_DIR)
            if file_errors:
                raise APIError(
                    'File validation failed',
                    ErrorCode.FILE_NOT_FOUND,
                    400,
                    {'errors': file_errors}
                )
        
        # Process with managed process
        stream = data.get('stream', False)
        with managed_process(['gemini'], ...) as process:
            # ... process request ...
            
    except APIError:
        raise
    except Exception as e:
        app.logger.exception("Generate error")
        raise APIError('Generation failed', ErrorCode.GEMINI_CLI_ERROR, 500)
```

## Implementation Phases

### Phase 1: Critical Security & Error Handling (Week 1)

1. **Install dependencies**
   ```bash
   pip install flask-limiter python-magic werkzeug
   ```

2. **Add configuration**
   - Copy `config.py` to your project
   - Create `.env` file with your settings

3. **Add validators**
   - Copy `utils/validators.py`
   - Update upload endpoint to use validators

4. **Add error handling**
   - Copy `utils/errors.py`
   - Add error handlers to app

5. **Add process management**
   - Copy `utils/process_manager.py`
   - Update subprocess calls to use `managed_process`

6. **Add logging**
   - Copy `utils/logging_config.py`
   - Call `setup_logging(app)` in app initialization

### Phase 2: Database & Session Management (Week 2)

1. **Install database dependencies**
   ```bash
   pip install flask-sqlalchemy alembic
   ```

2. **Create database models**
   ```python
   # models.py
   from flask_sqlalchemy import SQLAlchemy
   from datetime import datetime, timedelta
   
   db = SQLAlchemy()
   
   class UploadedFile(db.Model):
       # ... model definition ...
   ```

3. **Add database initialization**
   ```python
   from models import db
   db.init_app(app)
   with app.app_context():
       db.create_all()
   ```

4. **Update file management to use database**

### Phase 3: Caching & Performance (Week 3)

1. **Install Redis and caching**
   ```bash
   pip install redis flask-caching
   ```

2. **Setup caching**
   ```python
   from flask_caching import Cache
   cache = Cache(app, config={'CACHE_TYPE': 'redis', ...})
   ```

3. **Add caching to generate endpoint**

### Phase 4: Monitoring & Testing (Week 4)

1. **Add metrics**
   ```bash
   pip install prometheus-client
   ```

2. **Add health check endpoint**

3. **Write unit tests**
   ```bash
   pip install pytest pytest-flask pytest-cov
   ```

4. **Setup CI/CD**

## Testing the Improvements

### Test File Validation

```python
# test_upload.py
def test_upload_large_file(client):
    large_file = b'x' * (51 * 1024 * 1024)
    response = client.post('/api/upload', data={'file': (io.BytesIO(large_file), 'large.txt')})
    assert response.status_code == 400
    assert 'FILE_TOO_LARGE' in response.json['code']
```

### Test Rate Limiting

```python
def test_rate_limit(client):
    for i in range(6):
        response = client.post('/api/upload', data={'file': (io.BytesIO(b'test'), 'test.txt')})
    assert response.status_code == 429  # Too Many Requests
```

### Test Error Handling

```python
def test_invalid_prompt(client):
    response = client.post('/api/generate', json={'prompt': ''})
    assert response.status_code == 400
    assert 'VALIDATION_ERROR' in response.json['code']
```

## Migration Checklist

- [ ] Backup current `app.py`
- [ ] Install new dependencies
- [ ] Copy utility files (`config.py`, `utils/*`)
- [ ] Update `app.py` imports
- [ ] Update upload endpoint with validation
- [ ] Update generate endpoint with validation
- [ ] Add error handlers
- [ ] Add rate limiting
- [ ] Add process management
- [ ] Add logging
- [ ] Test all endpoints
- [ ] Update documentation
- [ ] Deploy to staging
- [ ] Monitor and adjust

## Rollback Plan

If issues occur:

1. Revert to previous `app.py`
2. Remove new dependencies: `pip uninstall flask-limiter python-magic`
3. Restore previous requirements.txt
4. Restart service

## Notes

- Start with Phase 1 improvements (security & error handling)
- Test thoroughly before moving to next phase
- Monitor logs and metrics after each phase
- Keep backups of working versions
- Document any customizations

