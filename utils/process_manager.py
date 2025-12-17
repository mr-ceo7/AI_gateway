"""
Process management utilities for subprocess handling.
"""
import subprocess
import signal
import atexit
import logging
from contextlib import contextmanager
from typing import Set, Optional
import os

logger = logging.getLogger(__name__)

# Track active processes for cleanup
active_processes: Set[subprocess.Popen] = set()


@contextmanager
def managed_process(cmd, **kwargs):
    """
    Context manager for subprocess with guaranteed cleanup.
    
    Args:
        cmd: Command to run
        **kwargs: Additional subprocess.Popen arguments
        
    Yields:
        subprocess.Popen: The process object
    """
    process: Optional[subprocess.Popen] = None
    try:
        process = subprocess.Popen(cmd, **kwargs)
        active_processes.add(process)
        logger.debug(f"Started process {process.pid} for command: {cmd}")
        yield process
    finally:
        if process:
            active_processes.discard(process)
            cleanup_process(process)


def cleanup_process(process: subprocess.Popen, timeout: int = 5):
    """
    Clean up a subprocess, terminating if necessary.
    
    Args:
        process: Process to clean up
        timeout: Timeout for graceful termination
    """
    if process.poll() is None:  # Process still running
        try:
            logger.debug(f"Terminating process {process.pid}")
            process.terminate()
            try:
                process.wait(timeout=timeout)
                logger.debug(f"Process {process.pid} terminated gracefully")
            except subprocess.TimeoutExpired:
                logger.warning(f"Process {process.pid} did not terminate, killing")
                process.kill()
                process.wait()
                logger.debug(f"Process {process.pid} killed")
        except ProcessLookupError:
            # Process already terminated
            logger.debug(f"Process {process.pid} already terminated")
        except Exception as e:
            logger.error(f"Error cleaning up process {process.pid}: {e}")


def cleanup_all_processes():
    """Cleanup all active processes on shutdown."""
    logger.info(f"Cleaning up {len(active_processes)} active processes")
    for process in list(active_processes):
        cleanup_process(process)
    active_processes.clear()


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown."""
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, cleaning up processes")
        cleanup_all_processes()
        os._exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    atexit.register(cleanup_all_processes)


# Initialize signal handlers on import
setup_signal_handlers()

