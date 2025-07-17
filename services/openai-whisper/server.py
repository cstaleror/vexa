import asyncio
import websockets
import json
import logging
import os
import tempfile
import time
from typing import Optional, Dict, Any
import soundfile as sf
import numpy as np
from openai import OpenAI
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OpenAIWhisperServer:
    def __init__(self):
        self.openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        self.redis_client = None
        self.model = os.getenv('OPENAI_WHISPER_MODEL', 'whisper-1')
        self.max_retries = int(os.getenv('OPENAI_MAX_RETRIES', '3'))
        self.timeout = int(os.getenv('OPENAI_TIMEOUT', '30'))
        
    async def init_redis(self):
        redis_host = os.getenv('REDIS_HOST', 'redis')
        redis_port = int(os.getenv('REDIS_PORT', '6379'))
        redis_db = int(os.getenv('REDIS_DB', '0'))
        
        self.redis_client = redis.Redis(
            host=redis_host, 
            port=redis_port, 
            db=redis_db,
            decode_responses=True
        )
        
    async def transcribe_audio(self, audio_data: bytes, language: str = None) -> str:
        """Transcribe audio using OpenAI Whisper API"""
        try:
            # Save audio to temporary file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                temp_file.write(audio_data)
                temp_file_path = temp_file.name
            
            # Transcribe with OpenAI
            with open(temp_file_path, 'rb') as audio_file:
                transcript = self.openai_client.audio.transcriptions.create(
                    model=self.model,
                    file=audio_file,
                    language=language,
                    response_format="text"
                )
            
            # Clean up temp file
            os.unlink(temp_file_path)
            
            return transcript
            
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return ""
    
    async def handle_client(self, websocket):
        """Handle WebSocket client connection"""
        client_id = f"client_{int(time.time())}"
        logger.info(f"Client {client_id} connected")
        
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    # Audio data received
                    transcript = await self.transcribe_audio(message)
                    
                    if transcript.strip():
                        # Send to Redis stream (maintaining compatibility)
                        stream_data = {
                            'text': transcript,
                            'timestamp': time.time(),
                            'client_id': client_id,
                            'language': 'auto',  # OpenAI auto-detects
                            'confidence': 1.0    # OpenAI doesn't provide confidence
                        }
                        
                        await self.redis_client.xadd(
                            'transcription_segments',
                            stream_data
                        )
                        
                        # Send back to client
                        response = {
                            'text': transcript,
                            'timestamp': time.time()
                        }
                        await websocket.send(json.dumps(response))
                        
                elif isinstance(message, str):
                    # Handle JSON messages (configuration, etc.)
                    try:
                        data = json.loads(message)
                        # Handle configuration messages if needed
                        logger.info(f"Received config: {data}")
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON received: {message}")
                        
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client {client_id} disconnected")
        except Exception as e:
            logger.error(f"Error handling client {client_id}: {e}")
        

    async def start_server(self):
        """Start the WebSocket server"""
        await self.init_redis()
        
        logger.info("Starting OpenAI Whisper WebSocket server on port 9090")
        
        # Start main server
        main_server = await websockets.serve(
            self.handle_client,
            "0.0.0.0",
            9090,
            ping_interval=20,
            ping_timeout=10
        )
        
        # Start health check server
        health_server = await websockets.serve(
            self.health_handler,
            "0.0.0.0",
            9091
        )
        
        logger.info("Health check server started on port 9091")
        
        # Keep servers running
        await asyncio.gather(
            main_server.wait_closed(),
            health_server.wait_closed()
        )

    async def health_handler(self, websocket, path):
        """Health check handler"""
        await websocket.send("OK")
        await websocket.close()

if __name__ == "__main__":
    server = OpenAIWhisperServer()
    asyncio.run(server.start_server())
