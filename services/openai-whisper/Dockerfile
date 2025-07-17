FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    build-essential \
    gcc \
    g++ \
    jq \
    curl \
    procps \
    net-tools \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY services/openai-whisper/requirements.txt .
RUN ls -la /app/
RUN cat requirements.txt

# Install requirements with verbose output
RUN pip install --no-cache-dir -r requirements.txt -v

# Copy application code
COPY services/openai-whisper/ .

# Create healthcheck script
COPY ./services/openai-whisper/healthcheck.sh /healthcheck.sh
RUN chmod +x /healthcheck.sh

EXPOSE 9090 9091

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD /healthcheck.sh

CMD ["python", "server.py"]
