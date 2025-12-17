"""
File validation and security utilities.
"""
import os
import magic
from pathlib import Path
from typing import List, Optional
from werkzeug.utils import secure_filename
from config import config


class ValidationError(Exception):
    """Raised when file validation fails."""
    pass


def validate_file(file_content: bytes, filename: str) -> List[str]:
    """
    Comprehensive file validation.
    
    Args:
        file_content: Raw file content bytes
        filename: Original filename
        
    Returns:
        List of validation error messages (empty if valid)
        
    Raises:
        ValidationError: If validation fails critically
    """
    errors = []
    
    # Size check
    if len(file_content) > config.MAX_FILE_SIZE:
        max_mb = config.MAX_FILE_SIZE / (1024 * 1024)
        errors.append(f"File exceeds maximum size of {max_mb}MB")
    
    # Filename security check
    if not filename or filename.strip() == '':
        errors.append("Filename is required")
    
    # Path traversal protection
    if '..' in filename or '/' in filename or '\\' in filename:
        errors.append("Invalid filename: path traversal detected")
    
    # Extension check
    ext = Path(filename).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        errors.append(f"File extension {ext} not allowed. Allowed: {', '.join(config.ALLOWED_EXTENSIONS)}")
    
    # MIME type check (more secure than extension)
    try:
        mime = magic.Magic(mime=True)
        detected_mime = mime.from_buffer(file_content[:1024])
        if detected_mime not in config.ALLOWED_MIME_TYPES:
            errors.append(f"File type {detected_mime} not allowed. Detected MIME type doesn't match allowed types.")
    except ImportError:
        # python-magic not installed, skip MIME check
        pass
    except Exception as e:
        # MIME detection failed, log but don't fail
        import logging
        logging.warning(f"MIME detection failed: {e}")
    
    # Content validation for specific types
    if ext == '.pdf':
        if not file_content.startswith(b'%PDF'):
            errors.append("File does not appear to be a valid PDF")
    
    return errors


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent security issues.
    
    Args:
        filename: Original filename
        
    Returns:
        Sanitized filename
    """
    # Use werkzeug's secure_filename
    safe_name = secure_filename(filename)
    
    # Additional sanitization
    if not safe_name:
        safe_name = "uploaded_file"
    
    # Limit length
    if len(safe_name) > 255:
        name, ext = os.path.splitext(safe_name)
        safe_name = name[:250] + ext
    
    return safe_name


def sanitize_prompt(prompt: str) -> str:
    """
    Sanitize prompt to prevent command injection.
    
    Args:
        prompt: User-provided prompt
        
    Returns:
        Sanitized prompt
        
    Raises:
        ValidationError: If prompt is invalid
    """
    if not prompt or not prompt.strip():
        raise ValidationError("Prompt cannot be empty")
    
    # Remove potentially dangerous characters
    prompt = prompt.replace('\x00', '')  # Null bytes
    prompt = prompt.replace('\r', '')     # Carriage returns
    
    # Limit length
    if len(prompt) > config.GEMINI_MAX_PROMPT_LENGTH:
        raise ValidationError(
            f"Prompt too long. Maximum length: {config.GEMINI_MAX_PROMPT_LENGTH} characters"
        )
    
    return prompt.strip()


def validate_file_list(files: List[str], upload_dir: str) -> List[str]:
    """
    Validate that file references exist and are accessible.
    
    Args:
        files: List of filenames
        upload_dir: Upload directory path
        
    Returns:
        List of validation errors (empty if valid)
    """
    errors = []
    
    for filename in files:
        # Security: prevent path traversal
        if '..' in filename or '/' in filename or '\\' in filename:
            errors.append(f"Invalid filename: {filename}")
            continue
        
        filepath = os.path.join(upload_dir, filename)
        
        # Check if file exists
        if not os.path.exists(filepath):
            errors.append(f"File not found: {filename}")
            continue
        
        # Check if file is within upload directory (prevent symlink attacks)
        real_upload_dir = os.path.realpath(upload_dir)
        real_filepath = os.path.realpath(filepath)
        if not real_filepath.startswith(real_upload_dir):
            errors.append(f"Invalid file path: {filename}")
            continue
        
        # Check file size (prevent reading huge files)
        file_size = os.path.getsize(filepath)
        if file_size > config.MAX_FILE_SIZE * 10:  # Allow 10x for extracted files
            errors.append(f"File too large: {filename}")
    
    return errors

