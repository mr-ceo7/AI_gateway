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

# Run a test command to trigger authentication.
# We use '|| true' to ensure the script continues even if gemini returns a non-zero exit code
# (which it might if auth is pending or fails initially).
# IMPORTANT: This output will appear in Render logs. Watch for the URL!
echo "Triggering Gemini CLI..."
# echo "Hello World" | gemini chat || true
gemini

echo "----------------------------------------------------------------"
echo "Starting Web Server..."
echo "----------------------------------------------------------------"

# Start the server
exec gunicorn --bind 0.0.0.0:$PORT app:app
