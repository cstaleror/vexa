#!/bin/bash
# Health check for OpenAI Whisper service
ps aux | grep "python server.py" | grep -v grep > /dev/null || exit 1