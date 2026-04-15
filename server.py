import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
import os
import time
import tempfile
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
from faster_whisper import WhisperModel

app = Flask(__name__)
CORS(app)

# Add error logging
import logging
logging.basicConfig(level=logging.DEBUG,
                   format='%(asctime)s - %(levelname)s - %(message)s')

# Load faster-whisper model with INT8 quantization for speed
model_name = os.environ.get('WHISPER_MODEL', 'small')
compute_type = os.environ.get('WHISPER_COMPUTE', 'int8')
cpu_threads = int(os.environ.get('WHISPER_THREADS', '0'))  # 0 = auto

logging.info(f"Loading faster-whisper model: {model_name} (compute: {compute_type})")
model = WhisperModel(
    model_name,
    device="cpu",
    compute_type=compute_type,
    cpu_threads=cpu_threads,
)
logging.info("Model loaded successfully")

@app.route('/')
def home():
    return {"status": "Speechfire server is running", "version": "2.0", "engine": "faster-whisper", "model": model_name, "compute": compute_type}

@app.route('/transcribe', methods=['POST'])
def transcribe():
    language = request.args.get('lang', 'Portuguese')

    if 'audio_data' not in request.files:
        return jsonify({"error": "No audio data found!"}), 400

    audio_file = request.files['audio_data']
    if audio_file.filename == '':
        return jsonify({"error": "No selected file!"}), 400

    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio_file:
            audio_file.save(temp_audio_file.name)
            logging.debug(f"Saved temporary file: {temp_audio_file.name}")

        start_time = time.time()

        # Map language names to ISO codes for faster-whisper
        lang_map = {
            "portuguese": "pt", "english": "en", "spanish": "es",
            "french": "fr", "german": "de", "italian": "it",
            "japanese": "ja", "chinese": "zh", "korean": "ko",
        }
        lang_code = lang_map.get(language.lower(), language.lower())
        # If still not a 2-3 char code, try first 2 chars
        if len(lang_code) > 3:
            lang_code = "pt"  # Safe fallback for this setup

        segments, info = model.transcribe(
            temp_audio_file.name,
            language=lang_code,
            beam_size=1,                     # Faster than default beam_size=5
            vad_filter=True,                 # Skip silence segments
            condition_on_previous_text=False, # Prevent repetition loops
        )
        transcription = " ".join([segment.text.strip() for segment in segments])

        elapsed = time.time() - start_time
        logging.info(f"Transcription ({elapsed:.2f}s): {transcription[:80]}...")

    except Exception as e:
        error_tb = traceback.format_exc()
        logging.error(f"Error during transcription: {str(e)}\n{error_tb}")
        return jsonify({
            "error": str(e),
            "traceback": error_tb,
            "details": {
                "language": language,
                "model": model_name,
                "compute": compute_type,
            }
        }), 500

    finally:
        if os.path.exists(temp_audio_file.name):
            os.remove(temp_audio_file.name)
            logging.debug(f"Removed temporary file: {temp_audio_file.name}")

    return jsonify({"transcription": transcription})

if __name__ == "__main__":
    # Configure host and port based on environment
    host = os.environ.get('SERVER_HOST', '0.0.0.0' if os.environ.get('DOCKER_ENV') else '127.0.0.1')
    port = int(os.environ.get('SERVER_PORT', 5000))

    logging.info(f"Server will run on {host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
