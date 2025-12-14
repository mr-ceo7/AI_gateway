#!/bin/bash
echo "Starting AI Gateway..."

# Check if we need to authenticate
# Note: This is a blocking step. The user must look at the Render logs,
# copy the URL, authenticate, and then the server will start.
# If already authenticated (volume mounted?), this might skip usually, 
# but the user asked to trigger auth.

echo "----------------------------------------------------------------"
echo "Initializing Gemini CLI Authentication..."
echo "Please check the logs below for an authentication URL."
echo "Copy and paste it into your browser to authorize."
echo "----------------------------------------------------------------"

# Check for API Key
if [ -z "$GEMINI_API_KEY" ]; then
    echo "WARNING: GEMINI_API_KEY not found!"
    echo "For Render deployment, please set the GEMINI_API_KEY environment variable."
    echo "Get one here: https://aistudio.google.com/app/apikey"
    # We attempt to run anyway, in case there's another auth method (e.g. volume mount)
fi

# Run a test command to check connectivity
echo "Checking Gemini CLI..."
# We use explicit 'chat' command with argument to avoid ambiguity
gemini chat "Hello World" || echo "WARNING: Gemini CLI check failed. Continuing start-up to enable Auth UI..."

echo "----------------------------------------------------------------"
echo "Starting Web Server with Auth UI..."
echo "----------------------------------------------------------------"


# Start the server
# Use gevent worker for streaming-friendly SSE and no timeout
exec gunicorn --bind 0.0.0.0:$PORT -k gevent --timeout 0 app:app
