FROM python:3.11-slim

# Install system dependencies and Node.js (for gemini-chat-cli)
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    build-essential \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs npm \
    && npm install -g @google/gemini-cli \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Application Code
COPY . .

# Make startup script executable
RUN chmod +x start.sh

# Expose port (Render uses PORT env var, but we bind to 5000 in script usually, or use $PORT)
ENV PORT=5000
EXPOSE 5000

# Entrypoint
CMD ["./start.sh"]
