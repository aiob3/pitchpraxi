import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
import os
import sys
import time
import tempfile
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

import logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s')

# --- Engine selection via SPEECHFIRE_ENGINE env var ---
ENGINE = os.environ.get('SPEECHFIRE_ENGINE', 'qwen3')  # "qwen3" or "whisper"

if ENGINE == 'qwen3':
    # Qwen3-ASR ONNX CPU pipeline
    QWEN_ONNX_DIR = os.environ.get('QWEN_ONNX_DIR', '/home/brito/repos/Qwen3-ASR-0.6B-ONNX-CPU/onnx_models')
    sys.path.insert(0, os.path.dirname(QWEN_ONNX_DIR))
    from onnx_inference import OnnxAsrPipeline

    logging.info(f"Loading Qwen3-ASR ONNX pipeline from {QWEN_ONNX_DIR}...")
    pipeline = OnnxAsrPipeline(onnx_dir=QWEN_ONNX_DIR, quantize="int8")
    logging.info("Qwen3-ASR pipeline ready")

    def transcribe_audio(audio_path, language):
        result = pipeline.transcribe(audio_path, language=language)
        return result["text"], result["timing"]

elif ENGINE == 'whisper':
    # faster-whisper (CTranslate2) fallback
    from faster_whisper import WhisperModel

    model_name = os.environ.get('WHISPER_MODEL', 'base')
    compute_type = os.environ.get('WHISPER_COMPUTE', 'int8')

    logging.info(f"Loading faster-whisper: {model_name} (compute: {compute_type})")
    whisper_model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
    logging.info("faster-whisper ready")

    LANG_MAP = {
        "portuguese": "pt", "english": "en", "spanish": "es",
        "french": "fr", "german": "de", "italian": "it",
        "japanese": "ja", "chinese": "zh", "korean": "ko",
    }

    def transcribe_audio(audio_path, language):
        lang_code = LANG_MAP.get(language.lower(), language.lower())
        if len(lang_code) > 3:
            lang_code = "pt"
        segments, info = whisper_model.transcribe(
            audio_path, language=lang_code,
            beam_size=1, vad_filter=True, condition_on_previous_text=False,
        )
        text = " ".join([s.text.strip() for s in segments])
        return text, {"total_s": 0}

else:
    raise ValueError(f"Unknown engine: {ENGINE}. Use 'qwen3' or 'whisper'.")


@app.route('/')
def home():
    return {"status": "Speechfire server is running", "version": "3.0", "engine": ENGINE}

@app.route('/transcribe', methods=['POST'])
def transcribe():
    language = request.args.get('lang', 'Portuguese')

    if 'audio_data' not in request.files:
        return jsonify({"error": "No audio data found!"}), 400

    audio_file = request.files['audio_data']
    if audio_file.filename == '':
        return jsonify({"error": "No selected file!"}), 400

    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            audio_file.save(tmp.name)
            tmp_path = tmp.name

        start_time = time.time()
        text, timing = transcribe_audio(tmp_path, language)
        elapsed = time.time() - start_time

        logging.info(f"Transcription ({elapsed:.2f}s, engine={ENGINE}): {text[:80]}...")

    except Exception as e:
        error_tb = traceback.format_exc()
        logging.error(f"Error during transcription: {str(e)}\n{error_tb}")
        return jsonify({
            "error": str(e),
            "traceback": error_tb,
            "details": {"language": language, "engine": ENGINE}
        }), 500

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return jsonify({"transcription": text})

if __name__ == "__main__":
    host = os.environ.get('SERVER_HOST', '0.0.0.0' if os.environ.get('DOCKER_ENV') else '127.0.0.1')
    port = int(os.environ.get('SERVER_PORT', 5000))

    logging.info(f"Server v3.0 ({ENGINE}) on {host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
