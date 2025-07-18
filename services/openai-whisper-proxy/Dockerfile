FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    jq \
    && rm -rf /var/lib/apt/lists/*

COPY services/openai-whisper-proxy/requirements.txt .

RUN pip install --no-cache -r requirements.txt

COPY services/openai-whisper-proxy/main.py .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]