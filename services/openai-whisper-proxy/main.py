import asyncio, io, json, os, uuid, logging
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # type: ignore
from openai import AsyncOpenAI  # type: ignore
import redis.asyncio as aioredis  # type: ignore
from datetime import datetime

# --- Config -------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL          = os.getenv("OPENAI_MODEL_NAME", "whisper-1")
CHUNK_SECONDS  = float(os.getenv("CHUNK_SECONDS", 5))
REDIS_HOST     = os.getenv("REDIS_HOST", "redis")
REDIS_PORT     = int(os.getenv("REDIS_PORT", 6379))
STREAM_NAME    = os.getenv("REDIS_STREAM_NAME", "transcription_segments")

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("whisper-proxy")

# --- FastAPI ------------------------------------------------
app = FastAPI()
redis: Optional[aioredis.Redis] = None

@app.on_event("startup")
async def _startup():
    global redis
    redis = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT,
                           decode_responses=True)

@app.on_event("shutdown")
async def _shutdown():
    if redis:
        await redis.close()

@app.get("/health")
def health():
    return {"status": "ok"}

# ---------------- WebSocket bridge -------------------------
@app.websocket("/ws")
async def ws_proxy(ws: WebSocket):
    await ws.accept()

    buf: bytes = b""
    speaker: str = "unknown"
    last_send: float = asyncio.get_event_loop().time()

    # Datos de la reunión enviados en el primer mensaje JSON del bot
    token: Optional[str] = None
    platform: Optional[str] = None
    meeting_id: Optional[str] = None
    uid: Optional[str] = None

    sent_session_start = False

    # Offset aproximado de la línea de tiempo de audio
    offset_sec: float = 0.0

    async def flush_audio(audio_bytes: bytes, current_speaker: str, nonlocal_offset: float) -> float:
        """Envía audio a OpenAI y publica en Redis con formato esperado. Devuelve nuevo offset."""
        nonlocal sent_session_start

        if not audio_bytes or not token or not platform or not meeting_id:
            log.debug("Flush_audio abortado: falta de contexto completo (token=%s, platform=%s, meeting_id=%s)", token, platform, meeting_id)
            return nonlocal_offset

        # 1️⃣ Publicar session_start la primera vez que tengamos contexto completo
        if redis and not sent_session_start:
            try:
                session_start_payload = {
                    "type": "session_start",
                    "token": token,
                    "platform": platform,
                    "meeting_id": meeting_id,
                    "uid": uid or str(uuid.uuid4()),
                    "start_timestamp": datetime.utcnow().isoformat() + "Z"
                }
                await redis.xadd(STREAM_NAME, {"payload": json.dumps(session_start_payload)})
                sent_session_start = True
                log.info("Session_start publicado en Redis para UID %s", session_start_payload["uid"])
            except Exception as e:
                log.error("Error publicando session_start: %s", e)

        # Convertir PCM (16kHz, 16-bit LE mono) a WAV
        import wave, struct
        fileobj = io.BytesIO()
        with wave.open(fileobj, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # 16-bit = 2 bytes
            wav.setframerate(16000)
            wav.writeframes(audio_bytes)
        fileobj.seek(0)
        fileobj.name = "chunk.wav"

        resp = await client.audio.transcriptions.create(
            model=MODEL,
            file=fileobj,
            response_format="json",
        )

        text = resp.text
        log.info("%s: %s", current_speaker, text)

        # Construir segmento único
        start_time = nonlocal_offset
        end_time = nonlocal_offset + CHUNK_SECONDS
        segment = {
            "text": text,
            "start": f"{start_time:.3f}",
            "end": f"{end_time:.3f}",
            "language": None,
        }

        payload = {
            "type": "transcription",
            "token": token,
            "platform": platform,
            "meeting_id": meeting_id,
            "segments": [segment],
            "uid": uid or str(uuid.uuid4()),
        }

        if redis:
            try:
                await redis.xadd(STREAM_NAME, {"payload": json.dumps(payload)})
            except Exception as e:
                log.error("Error publicando en Redis: %s", e)

        return end_time  # Nuevo offset

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            data_bytes = msg.get("bytes")
            data_text = msg.get("text")

            if data_bytes is not None:
                buf += data_bytes
                now = asyncio.get_event_loop().time()
                if now - last_send >= CHUNK_SECONDS:
                    offset_sec = await flush_audio(buf, speaker, offset_sec)
                    buf = b""
                    last_send = now

            elif data_text is not None:
                try:
                    obj = json.loads(data_text)

                    # Mensaje inicial con datos de la reunión
                    if all(k in obj for k in ("token", "platform", "meeting_id")):
                        token = obj.get("token")
                        platform = obj.get("platform")
                        meeting_id = obj.get("meeting_id")
                        uid = obj.get("uid") or uid
                        log.info("Config recibida: platform=%s meeting_id=%s", platform, meeting_id)
                        continue

                    # Actualización de speaker
                    if obj.get("type") == "speaker_activity":
                        payload_event = obj.get("payload", {})
                        ev = payload_event.get("event_type")
                        name_evt = payload_event.get("participant_name") or "unknown"
                        if ev == "SPEAKER_START":
                            speaker = name_evt
                        elif ev == "SPEAKER_END" and name_evt == speaker:
                            speaker = "unknown"
                except json.JSONDecodeError:
                    pass

    finally:
        if buf:
            offset_sec = await flush_audio(buf, speaker, offset_sec)
        await ws.close()

# Eliminada función process: ahora la lógica está en flush_audio
