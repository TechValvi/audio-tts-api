
import os
import io
import asyncio
import logging

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import edge_tts

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_VOICE = "gu-IN-DhwaniNeural"
MAX_TEXT_LENGTH = 5000


@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "message": "TechValvi Voice API is running"
    }), 200


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "માન્ય JSON ડેટા મોકલો."
        }), 400

    text = str(data.get("text") or "").strip()
    voice = str(data.get("voice") or DEFAULT_VOICE).strip()
    rate = str(data.get("rate") or "+0%").strip()
    pitch = str(data.get("pitch") or "+0Hz").strip()

    # edge-tts custom SSML સ્વીકારતું નથી.
    # તેથી ssml field ને સીધું TTS માં મોકલતા નથી.
    if not text:
        return jsonify({
            "error": "કૃપા કરીને બોલવાનું લખાણ દાખલ કરો."
        }), 400

    if len(text) > MAX_TEXT_LENGTH:
        return jsonify({
            "error": f"લખાણ {MAX_TEXT_LENGTH} અક્ષરથી ઓછું રાખો."
        }), 400

    # સામાન્ય લખાણ અને યોગ્ય voice જ વાપરો.
    if not voice.endswith("Neural") or len(voice) > 100:
        return jsonify({
            "error": "Voice પસંદગી યોગ્ય નથી."
        }), 400

    async def create_audio():
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate,
            pitch=pitch
        )

        audio = bytearray()

        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio.extend(chunk["data"])

        if not audio:
            raise RuntimeError(
                "TTS તરફથી ઓડિયો મળ્યો નથી."
            )

        return bytes(audio)

    try:
        audio_data = asyncio.run(create_audio())

        return send_file(
            io.BytesIO(audio_data),
            mimetype="audio/mpeg",
            as_attachment=False,
            download_name="speech.mp3"
        )

    except Exception:
        logger.exception("Audio generation failed")

        return jsonify({
            "error": (
                "ઓડિયો જનરેટ થઈ શક્યો નથી. "
                "થોડી વાર પછી ફરી પ્રયાસ કરો."
            )
        }), 502


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
