# Quick Reference - Improvements Checklist

## 🔴 Critical Improvements (Do First)

### Security
- [ ] Add file size limits (`MAX_FILE_SIZE` in config)
- [ ] Add MIME type validation (`python-magic`)
- [ ] Add path traversal protection
- [ ] Add rate limiting (`flask-limiter`)
- [ ] Sanitize prompts before subprocess calls

### Error Handling
- [ ] Replace generic exceptions with `APIError`
- [ ] Add structured error responses
- [ ] Add error logging
- [ ] Add error handlers to Flask app

### Process Management
- [ ] Use `managed_process` context manager
- [ ] Add signal handlers for cleanup
- [ ] Track active processes

### Logging
- [ ] Replace `print()` with `app.logger`
- [ ] Add structured JSON logging
- [ ] Add log rotation
- [ ] Add request ID tracking

## 🟡 High Priority (Do Next)

### Configuration
- [ ] Move hardcoded values to `config.py`
- [ ] Use environment variables
- [ ] Create `.env` file

### Database
- [ ] Add SQLAlchemy models
- [ ] Store file metadata in database
- [ ] Add session management
- [ ] Add cleanup jobs

### Health Checks
- [ ] Add `/health` endpoint
- [ ] Check upload directory
- [ ] Check Gemini CLI availability

## 🟢 Medium Priority (Do Later)

### Performance
- [ ] Add Redis caching
- [ ] Add retry logic with backoff
- [ ] Add connection pooling

### Monitoring
- [ ] Add Prometheus metrics
- [ ] Add `/metrics` endpoint
- [ ] Track request duration
- [ ] Track error rates

### Testing
- [ ] Write unit tests
- [ ] Write integration tests
- [ ] Add test coverage

## Code Changes Checklist

### app.py Updates
```python
# Add imports
from config import config
from utils.errors import APIError, ErrorCode
from utils.validators import validate_file, sanitize_prompt
from utils.process_manager import managed_process
from utils.logging_config import setup_logging

# Initialize
setup_logging(app)

# Update endpoints
@app.route('/api/upload', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_UPLOAD)
def upload_file():
    # Add validation
    errors = validate_file(file_content, filename)
    if errors:
        raise APIError(...)
    # Use managed_process
    # Log with app.logger
```

## Dependencies to Install

```bash
# Critical
pip install flask-limiter python-magic werkzeug

# High Priority
pip install flask-sqlalchemy alembic

# Medium Priority
pip install redis flask-caching backoff prometheus-client
```

## Environment Variables

```bash
# Required
SECRET_KEY=your-secret-key
MAX_FILE_SIZE=52428800

# Optional
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=sqlite:///ai_gateway.db
RATE_LIMIT_ENABLED=True
```

## Testing Checklist

- [ ] Test file size limits
- [ ] Test invalid file types
- [ ] Test rate limiting
- [ ] Test error handling
- [ ] Test process cleanup
- [ ] Test logging output
- [ ] Test health endpoint

## Deployment Checklist

- [ ] Update requirements.txt
- [ ] Set environment variables
- [ ] Initialize database
- [ ] Setup log rotation
- [ ] Configure rate limiting
- [ ] Test in staging
- [ ] Monitor after deployment

## Common Issues & Solutions

### Issue: Rate limiting not working
**Solution**: Check `RATE_LIMIT_STORAGE` config, ensure Redis is running if using Redis

### Issue: File validation too strict
**Solution**: Adjust `ALLOWED_EXTENSIONS` and `ALLOWED_MIME_TYPES` in config

### Issue: Processes not cleaning up
**Solution**: Ensure `managed_process` context manager is used correctly

### Issue: Logs not rotating
**Solution**: Check log directory permissions and `LOG_MAX_BYTES` config

## Performance Targets

- Upload: < 2 seconds for 10MB file
- Generate: < 5 seconds for simple prompt
- Error rate: < 1%
- Uptime: > 99.9%

## Security Checklist

- [ ] File size limits enforced
- [ ] File type validation working
- [ ] Rate limiting active
- [ ] Input sanitization in place
- [ ] Path traversal prevented
- [ ] Secure session management
- [ ] Error messages don't leak info

---

**Start with Critical improvements, test thoroughly, then proceed to High Priority items.**

