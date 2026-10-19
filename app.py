import os
import io
import re
import asyncio
import logging
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pydub import AudioSegment
import imageio_ffmpeg
import edge_tts
from gtts import gTTS

app = Flask(__name__)
CORS(app)

# --------------------------------------------------
# TechValvi Voice Studio
# Gujarati + Hindi + English Text-to-Speech API
# --------------------------------------------------

app.config["MAX_CONTENT_LENGTH"] = 100 * 1024  # 100 KB
MAX_TEXT_LENGTH = 1000

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("techvalvi-voice")

try:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    AudioSegment.converter = ffmpeg_exe
except Exception as e:
    logger.warning("FFmpeg પાથ સેટ કરવામાં ચેતવણી: %s", e)

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


def _number_from_value(value, suffix=""):
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else 0.0


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def generate_audio_gtts_fallback(text, voice):
    """Edge-TTS બ્લોક થાય ત્યારે Google TTS દ્વારા ઓડિયો બનાવે છે."""
    logger.info("Edge-TTS ફેલ થયું. gTTS ફોલબેક શરૂ થઈ રહ્યું છે...")
    lang = VOICE_LANGUAGE.get(voice, "gu")
    
    tts = gTTS(text=text, lang=lang, slow=False)
    buffer = io.BytesIO()
    tts.write_to_fp(buffer)
    buffer.seek(0)
    return buffer


async def generate_audio(text, voice, rate, pitch, emotion="natural"):
    text = text.strip()
    if not text:
        raise ValueError("ઓડિયો બનાવવા માટે લખાણ ખાલી છે.")

    try:
        base_rate = _number_from_value(rate)
        base_pitch = _number_from_value(pitch)

        emotion_adjustments = {
            "natural":      (0, 0),
            "friendly":     (2, 1),
            "happy":        (4, 2),
            "laugh":        (4, 3),
            "excited":      (5, 3),
            "sad":          (-5, -2),
            "fear":         (-7, 1),
            "warning":      (-4, -1),
            "serious":      (-4, -1),
            "angry":        (3, 2),
            "question":     (-3, 3),
            "calm":         (-6, -1),
            "storytelling": (-4, 1),
            "urgent":       (5, 2),
        }

        emotion = str(emotion).lower().strip()
        rate_change, pitch_change = emotion_adjustments.get(emotion, (0, 0))

        final_rate = _clamp(base_rate + rate_change, -30, 30)
        final_pitch = _clamp(base_pitch + pitch_change, -15, 15)

        rate_value = f"{final_rate:+g}%"
        pitch_value = f"{final_pitch:+g}Hz"

        sentences = re.findall(
            r".+?(?:[?!।.!！？]+(?=\s|$)|$)",
            text,
            flags=re.DOTALL
        )
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            sentences = [text]

        combined_audio = AudioSegment.empty()

        for index, sentence in enumerate(sentences):
            audio_buffer = io.BytesIO()
            communicate = edge_tts.Communicate(
                text=sentence,
                voice=voice,
                rate=rate_value,
                pitch=pitch_value,
            )

            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    audio_buffer.write(chunk["data"])

            if audio_buffer.getbuffer().nbytes == 0:
                raise RuntimeError(f"વાક્ય {index + 1} માટે ઓડિયો મળ્યો નથી.")

            audio_buffer.seek(0)
            sentence_audio = AudioSegment.from_file(audio_buffer, format="mp3")
            combined_audio += sentence_audio

            if index < len(sentences) - 1:
                ending = sentence.rstrip()[-1]
                if ending in ("?", "？"):
                    pause_ms = 700
                elif ending in ("!", "！"):
                    pause_ms = 550
                elif ending in ("।", "."):
                    pause_ms = 500
                else:
                    pause_ms = 350

                if emotion in ("fear", "storytelling", "calm"):
                    pause_ms += 200
                elif emotion in ("urgent", "excited"):
                    pause_ms = max(250, pause_ms - 150)

                combined_audio += AudioSegment.silent(
                    duration=pause_ms,
                    frame_rate=24000
                )

        output_buffer = io.BytesIO()
        combined_audio.export(output_buffer, format="mp3", bitrate="48k")
        output_buffer.seek(0)
        return output_buffer

    except Exception as edge_err:
        logger.warning("Edge-TTS એરર (%s). gTTS સ્વિચ થાય છે...", edge_err)
        return generate_audio_gtts_fallback(text, voice)


def prepare_gujarati_text(text):
    if not isinstance(text, str):
        return ""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if not text:
        return ""
    text = re.sub(r"\s+([?!,;:।])", r"\1", text)
    text = re.sub(r"([?!])(?=[^\s?!])", r"\1 ", text)
    text = re.sub(r"([।])(?=[^\s])", r"\1 ", text)
    text = re.sub(r"\?{2,}", "?", text)
    text = re.sub(r"!{2,}", "!", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "service": "TechValvi Voice Studio API",
        "status": "online",
        "languages": ["Gujarati", "Hindi", "English"],
        "endpoint": "/generate",
        "method": "POST",
    }), 200


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return json_error("માન્ય JSON request મોકલો.", 400)

    text = data.get("text", "")
    voice = data.get("voice", DEFAULT_VOICE)

    if not isinstance(text, str):
        return json_error("લખાણ યોગ્ય ફોર્મેટમાં મોકલો.", 400)

    text = prepare_gujarati_text(text)
    if not text.strip():
        return json_error("કૃપા કરીને લખાણ દાખલ કરો.", 400)

    if len(text) > MAX_TEXT_LENGTH:
        return json_error(f"લખાણ મહત્તમ {MAX_TEXT_LENGTH} અક્ષરનું હોવું જોઈએ.", 413)

    if not isinstance(voice, str) or voice not in VOICE_LANGUAGE:
        return json_error("પસંદ કરેલો વોઇસ મંજૂર નથી.", 400)

    language = data.get("language")
    if language is not None:
        if not isinstance(language, str):
            return json_error("ભાષાની પસંદગી અમાન્ય છે.", 400)
        if language not in ("gu", "hi", "en"):
            return json_error("ભાષા gu, hi અથવા en હોવી જોઈએ.", 400)
        if VOICE_LANGUAGE[voice] != language:
            return json_error("પસંદ કરેલો વોઇસ અને ભાષા મેળ ખાતા નથી.", 400)

    emotion = data.get("emotion", "natural")
    ALLOWED_EMOTIONS = {
        "natural", "friendly", "happy", "laugh",
        "excited", "sad", "fear", "warning",
        "serious", "angry", "question", "calm",
        "storytelling", "urgent"
    }

    if not isinstance(emotion, str) or emotion not in ALLOWED_EMOTIONS:
        return json_error("ઇમોશન સેટિંગ અમાન્ય છે.", 400)

    try:
        raw_rate = data.get("rate", 0)
        raw_pitch = data.get("pitch", "+0Hz")
        if isinstance(raw_rate, (int, float)) and not isinstance(raw_rate, bool):
            raw_rate = f"{raw_rate:+g}%"

        rate = validate_rate(raw_rate)
        pitch = validate_pitch(raw_pitch)
    except (ValueError, TypeError) as error:
        return json_error(str(error), 400)

    logger.info("TTS request: voice=%s, emotion=%s, rate=%s, pitch=%s", voice, emotion, rate, pitch)

    try:
        audio_buffer = asyncio.run(
            generate_audio(
                text=text,
                voice=voice,
                rate=rate,
                pitch=pitch,
                emotion=emotion
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
        logger.exception("TTS generation failed completely for voice %s", voice)
        return json_error("ઓડિયો બનાવી શકાયો નથી. કૃપા કરીને ફરી પ્રયાસ કરો.", 500)


@app.errorhandler(413)
def request_too_large(_error):
    return json_error("Requestનું કદ મંજૂર મર્યાદા કરતાં વધારે છે.", 413)


@app.errorhandler(404)
def not_found(_error):
    return json_error("આ API endpoint મળ્યો નથી.", 404)


@app.errorhandler(405)
def method_not_allowed(_error):
    return json_error("આ HTTP method મંજૂર નથી.", 405)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "1000"))
    app.run(host="0.0.0.0", port=port, debug=False)
