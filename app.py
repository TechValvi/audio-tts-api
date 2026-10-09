import os
import io
import asyncio
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import edge_tts

app = Flask(__name__)
CORS(app)

@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "API is running successfully!"})

@app.route('/generate', methods=['POST'])
def generate():
    data = request.get_json()
    text = data.get('text', '').strip()
    voice = data.get('voice', 'gu-IN-DhwaniNeural')
    rate = data.get('rate', '+0%')
    pitch = data.get('pitch', '+0Hz')

    if not text:
        return jsonify({'error': 'કૃપા કરીને લખાણ દાખલ કરો.'}), 400

    async def get_audio_bytes():
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate,
            pitch=pitch
        )
        audio_stream = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_stream.extend(chunk["data"])
        return bytes(audio_stream)

    try:
        audio_data = asyncio.run(get_audio_bytes())
        return send_file(
            io.BytesIO(audio_data),
            mimetype="audio/mpeg",
            as_attachment=False,
            download_name="speech.mp3"
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
