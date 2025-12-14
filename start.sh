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

# Check for credentials using credential inspection (not CLI probing)
echo "Checking for Gemini CLI credentials..."
if [ -f ~/.gemini/oauth_creds.json ]; then
    echo "✓ Found OAuth credentials at ~/.gemini/oauth_creds.json"
    CREDS_FOUND=true
elif [ -f ~/.gemini/settings.json ] && [ -f ~/.gemini/google_accounts.json ]; then
    echo "✓ Found Gemini CLI settings and active account"
    CREDS_FOUND=true
else
    echo "⚠ No credentials found. Auth UI will be available on startup."
    CREDS_FOUND=false
fi

echo "----------------------------------------------------------------"
echo "Starting Web Server with Auth UI..."
echo "----------------------------------------------------------------"


# Start the server
# Use sync worker with timeout 0 to disable for long-running streaming requests
exec gunicorn --bind 0.0.0.0:$PORT --timeout 0 app:app
