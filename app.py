from pydub import AudioSegment
import imageio_ffmpeg
import asyncio
import io
import logging
import os
import re


import edge_tts
from flask import Flask, jsonify, request, send_file

app = Flask(__name__)

# --------------------------------------------------
# TechValvi Voice Studio
# Gujarati + Hindi + English Text-to-Speech API
# --------------------------------------------------

app.config["MAX_CONTENT_LENGTH"] = 100 * 1024  # 100 KB
MAX_TEXT_LENGTH = 5000

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("techvalvi-voice")

# ભાષા પ્રમાણે મંજૂર વોઇસ
VOICE_LANGUAGE = {
    "gu-IN-DhwaniNeural": "gu",
    "gu-IN-NiranjanNeural": "gu",

    "hi-IN-SwaraNeural": "hi",
    "hi-IN-MadhurNeural": "hi",

    "en-IN-NeerjaNeural": "en",
    "en-IN-PrabhatNeural": "en",
}

DEFAULT_VOICE = "gu-IN-DhwaniNeural"

RATE_PATTERN = re.compile(r"^([+-]?\d{1,2})%$")
PITCH_PATTERN = re.compile(r"^([+-]?\d{1,2})Hz$")


def json_error(message, status=400):
    return jsonify({"error": message}), status


def validate_rate(value):
    if not isinstance(value, str):
        raise ValueError("સ્પીડનું ફોર્મેટ અમાન્ય છે.")

    match = RATE_PATTERN.fullmatch(value.strip())

    if not match:
        raise ValueError("સ્પીડ +0% જેવા ફોર્મેટમાં આપો.")

    number = int(match.group(1))

    if not -40 <= number <= 40:
        raise ValueError("સ્પીડ -40% થી +40% વચ્ચે હોવી જોઈએ.")

    return f"{number:+d}%"


def validate_pitch(value):
    if not isinstance(value, str):
        raise ValueError("પિચનું ફોર્મેટ અમાન્ય છે.")

    match = PITCH_PATTERN.fullmatch(value.strip())

    if not match:
        raise ValueError("પિચ +0Hz જેવા ફોર્મેટમાં આપો.")

    number = int(match.group(1))

    if not -20 <= number <= 20:
        raise ValueError("પિચ -20Hz થી +20Hz વચ્ચે હોવી જોઈએ.")

    return f"{number:+d}Hz"



async def generate_audio(text, voice, rate, pitch):
    """વાક્યો પ્રમાણે MP3 બનાવી વચ્ચે વાસ્તવિક મૌન ઉમેરે છે."""

    # imageio-ffmpeg દ્વારા FFmpeg બાઇનરી શોધો
    AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()

    # પ્રશ્નચિહ્ન અને પૂર્ણવિરામ સહિત વાક્યો અલગ કરો
    sentences = re.split(
        r'(?<=[.!?।！？])\s+',
        text.strip()
    )

    sentences = [
        sentence.strip()
        for sentence in sentences
        if sentence.strip()
    ]

    if not sentences:
        raise ValueError("ઓડિયો બનાવવા માટે લખાણ ખાલી છે.")

    combined_audio = AudioSegment.empty()

    for index, sentence in enumerate(sentences):
        audio_part = io.BytesIO()

        communicate = edge_tts.Communicate(
            text=sentence,
            voice=voice,
            rate=rate,
            pitch=pitch,
        )

        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio_part.write(chunk["data"])

        if audio_part.tell() == 0:
            raise RuntimeError("વાક્ય માટે ઓડિયો મળ્યો નથી.")

        audio_part.seek(0)

        sentence_audio = AudioSegment.from_file(
            audio_part,
            format="mp3"
        )

        combined_audio += sentence_audio

        # છેલ્લું વાક્ય ન હોય ત્યારે વિરામ ઉમેરો
        if index < len(sentences) - 1:
            ending = sentence[-1]

            if ending in ("?", "？"):
                pause_ms = 650
            elif ending in ("!", "！"):
                pause_ms = 500
            elif ending in ("।", "."):
                pause_ms = 450
            else:
                pause_ms = 300

            combined_audio += AudioSegment.silent(
                duration=pause_ms,
                frame_rate=24000
            )

    output_buffer = io.BytesIO()

    combined_audio.export(
        output_buffer,
        format="mp3",
        bitrate="48k"
    )

    output_buffer.seek(0)

    if output_buffer.getbuffer().nbytes == 0:
        raise RuntimeError("TTS સર્વરે ખાલી ઓડિયો આપ્યો.")

    return output_buffer

def prepare_gujarati_text(text):
    """ગુજરાતી TTS માટે લખાણ અને વાક્યવિરામ સુધારે છે."""

    if not isinstance(text, str):
        return ""

    # વધારાની ખાલી જગ્યાઓ દૂર કરો
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    if not text:
        return ""

    # પ્રશ્નચિહ્ન પહેલાંની બિનજરૂરી જગ્યાઓ દૂર કરો
    text = re.sub(r"\s+([?!,;:।])", r"\1", text)

    # પ્રશ્નચિહ્ન પછી જગ્યા ન હોય તો ઉમેરો
    text = re.sub(r"([?!])(?=[^\s?!])", r"\1 ", text)

    # પૂર્ણવિરામ પછી જગ્યા ન હોય તો ઉમેરો
    text = re.sub(r"([।])(?=[^\s])", r"\1 ", text)

    # એકસાથે આવેલા અનેક પ્રશ્નચિહ્નોને એકમાં ફેરવો
    text = re.sub(r"\?{2,}", "?", text)

    # સતત આવેલા અનેક આશ્ચર્યચિહ્નોને એકમાં ફેરવો
    text = re.sub(r"!{2,}", "!", text)

    # વધારાની જગ્યાઓ ફરીથી દૂર કરો
    text = re.sub(r" {2,}", " ", text)

    return text.strip()


@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health_check():
    """Render health check."""
    return jsonify({
        "service": "TechValvi Voice Studio API",
        "status": "online",
        "languages": ["Gujarati", "Hindi", "English"],
        "endpoint": "/generate",
        "method": "POST",
    }), 200


@app.route("/generate", methods=["POST"])

def generate():
    """લખાણમાંથી MP3 બનાવે છે."""

    data = request.get_json(silent=True)

    # 1. JSON request તપાસો
    if not isinstance(data, dict):
        return json_error(
            "માન્ય JSON request મોકલો.",
            400
        )

    # 2. લખાણ અને વોઇસ મેળવો
    text = data.get("text", "")
    voice = data.get("voice", DEFAULT_VOICE)

    if not isinstance(text, str):
        return json_error(
            "લખાણ યોગ્ય ફોર્મેટમાં મોકલો.",
            400
        )

    text = prepare_gujarati_text(text)

    if not text.strip():
        return json_error(
            "કૃપા કરીને લખાણ દાખલ કરો.",
            400
        )

    text = text.strip()

    if len(text) > MAX_TEXT_LENGTH:
        return json_error(
            f"લખાણ મહત્તમ {MAX_TEXT_LENGTH} અક્ષરનું હોવું જોઈએ.",
            413
        )

    # 3. વોઇસ તપાસો
    if not isinstance(voice, str) or voice not in VOICE_LANGUAGE:
        return json_error(
            "પસંદ કરેલો વોઇસ મંજૂર નથી.",
            400
        )

    # 4. ભાષા આપવામાં આવી હોય તો વોઇસ સાથે મેળ તપાસો
    language = data.get("language")

    if language is not None:
        if not isinstance(language, str):
            return json_error(
                "ભાષાની પસંદગી અમાન્ય છે.",
                400
            )

        if language not in ("gu", "hi", "en"):
            return json_error(
                "ભાષા gu, hi અથવા en હોવી જોઈએ.",
                400
            )

        if VOICE_LANGUAGE[voice] != language:
            return json_error(
                "પસંદ કરેલો વોઇસ અને ભાષા એકબીજા સાથે મેળ ખાતા નથી.",
                400
            )

    # 5. ઇમોશન તપાસો
    emotion = data.get("emotion", "natural")

    ALLOWED_EMOTIONS = {
        "natural", "friendly", "happy", "laugh",
        "excited", "sad", "fear", "warning",
        "serious", "angry", "question", "calm",
        "storytelling", "urgent"
    }

    if not isinstance(emotion, str) or emotion not in ALLOWED_EMOTIONS:
        return json_error(
            "ઇમોશન સેટિંગ અમાન્ય છે.",
            400
        )

    # 6. Rate અને Pitch સેટ કરો
    try:
        raw_rate = data.get("rate", 0)
        raw_pitch = data.get("pitch", "+0Hz")

        # Frontend numeric rate મોકલે તો તેને Edge TTS ફોર્મેટમાં ફેરવો
        if (
            isinstance(raw_rate, (int, float))
            and not isinstance(raw_rate, bool)
        ):
            raw_rate = f"{raw_rate:+g}%"

        rate = validate_rate(raw_rate)
        pitch = validate_pitch(raw_pitch)

    except (ValueError, TypeError) as error:
        return json_error(
            str(error),
            400
        )

    logger.info(
        "TTS request: voice=%s, emotion=%s, rate=%s, pitch=%s",
        voice, emotion, rate, pitch
    )

    # 7. MP3 ઓડિયો બનાવો
    try:
        audio_buffer = asyncio.run(
            generate_audio(
                text,
                voice,
                rate,
                pitch
            )
        )

        response = send_file(
            audio_buffer,
            mimetype="audio/mpeg",
            as_attachment=False,
            download_name="techvalvi-voice.mp3",
            max_age=0,
        )

        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"

        return response

    except Exception:
        logger.exception(
            "TTS generation failed for voice %s",
            voice
        )

        return json_error(
            "ઓડિયો બનાવી શકાયો નથી. વોઇસની ઉપલબ્ધતા અને સર્વર લોગ તપાસો.",
            502
        )


@app.errorhandler(413)
def request_too_large(_error):
    return json_error(
        "Requestનું કદ મંજૂર મર્યાદા કરતાં વધારે છે.",
        413
    )


@app.errorhandler(404)
def not_found(_error):
    return json_error("આ API endpoint મળ્યો નથી.", 404)


@app.errorhandler(405)
def method_not_allowed(_error):
    return json_error("આ HTTP method મંજૂર નથી.", 405)


if __name__ == "__main__":
    # સ્થાનિક પરીક્ષણ માટે; Renderમાં Gunicorn વાપરો.
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
