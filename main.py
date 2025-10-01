import os, time, re, logging, requests
from urllib.parse import urlparse, parse_qs

import assemblyai as aai
import google.generativeai as genai

from flask import Flask, request, jsonify

# ================== LOGGING CONFIG ==================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
logging.getLogger("assemblyai").setLevel(logging.ERROR)
logging.getLogger("google.generativeai").setLevel(logging.ERROR)

# ================== CONST ==================
BASE = "https://api-edu.go.vn/ioe-service/v2/game"

# Lấy API key từ biến môi trường (Railway Variables)
aai.settings.api_key = os.getenv("ASSEMBLYAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ================== GEMINI ==================
try:
    genai.configure(api_key=GEMINI_API_KEY)
    client = genai.GenerativeModel("gemini-2.5-flash")
except Exception as e:
    client = None
    logging.error("⚠️ Không thể khởi tạo Gemini Client: %s", e)


# ================== FUNCTIONS ==================
def audio_to_text(url: str) -> str:
    logging.info(f"[Audio] Đang chuyển audio sang text: {url}")
    config = aai.TranscriptionConfig(speech_model=aai.SpeechModel.universal)
    transcriber = aai.Transcriber(config=config)
    transcript = transcriber.transcribe(url)
    while transcript.status in ["queued", "processing"]:
        time.sleep(1)
        transcript = transcriber.get_transcript(transcript.id)
    if transcript.status == "error":
        raise RuntimeError(f"Transcription failed: {transcript.error}")
    text = transcript.text.strip().lower()
    text = re.sub(r'[^\w\s]', '', text)
    logging.info(f"[Audio] Transcript: {text}")
    return text


def fill_mask_with_gemini(masked_sentence: str, audio_transcript: str = "") -> str:
    if not client:
        return ""
    system_prompt = (
        "You are a helpful assistant. Fill in the missing word(s). "
        "Only return the characters being hidden by '*'."
    )
    if audio_transcript:
        user_prompt = f"Sentence: \"{masked_sentence}\" Transcript: \"{audio_transcript}\""
    else:
        user_prompt = f"Sentence: \"{masked_sentence}\""
    try:
        response = client.generate_content([system_prompt, user_prompt])
        ans = response.text.strip().lower()
        logging.info(f"[Gemini] Đáp án nhận được: {ans}")
        return ans
    except Exception as e:
        logging.error(f"[Gemini] Lỗi: {e}")
        return ""


def get_token_from_url(url: str) -> str:
    try:
        q = parse_qs(urlparse(url).query)
        return q.get("token", [None])[0]
    except Exception:
        return None


def post_json(path: str, payload: dict) -> dict:
    url = f"{BASE}/{path}"
    r = requests.post(url, json=payload, timeout=15)
    try:
        return r.json()
    except:
        return {"raw": r.text, "status": r.status_code}


def get_info(token: str) -> dict:
    payload = {"IPClient": "", "api_key": "gameioe", "deviceId": "",
               "serviceCode": "IOE", "token": token}
    return post_json("getinfo", payload)


def start_game(token: str, examKey: str) -> dict:
    payload = {"api_key": "gameioe", "serviceCode": "IOE", "token": token,
               "gameId": 0, "examKey": examKey, "deviceId": "", "IPClient": ""}
    return post_json("startgame", payload)


def finish_game(token: str, examKey: str, answers: list) -> dict:
    payload = {"api_key": "gameioe", "token": token, "serviceCode": "IOE",
               "examKey": examKey, "ans": answers, "IPClient": "", "deviceId": ""}
    return requests.post(f"{BASE}/finishgame", json=payload, timeout=15).json()


def run(link: str, delay: float = 0.6):
    token = get_token_from_url(link)
    if not token:
        return {"error": "Không tìm thấy token."}
    info = get_info(token)
    if not info.get("IsSuccessed"):
        return {"error": "getinfo fail", "detail": info}

    tokenrq = info["data"]["token"]
    examKey = info["data"]["game"]["examKey"]
    questions = info["data"]["game"]["question"] or []

    start_game(tokenrq, examKey)
    answers = []

    for q in questions:
        qid, qtype, point = q["id"], q.get("type"), q.get("Point", 10)
        masked_raw = q.get("content", {}).get("content", "")

        chosen = ""
        if qtype == 2:  # nghe điền từ
            audio_url = q.get("Description", {}).get("content")
            if audio_url:
                try:
                    transcript = audio_to_text(audio_url)
                    chosen = fill_mask_with_gemini(masked_raw, transcript)
                except Exception as e:
                    logging.error(f"[Audio] Lỗi: {e}")
                    chosen = fill_mask_with_gemini(masked_raw)
        elif qtype == 8:  # điền từ bình thường
            chosen = fill_mask_with_gemini(masked_raw)
        else:
            chosen = "A"  # placeholder cho loại câu hỏi khác

        answers.append({"questId": qid, "ans": chosen, "Point": point})

    fin = finish_game(tokenrq, examKey, answers)
    return {"questions": len(questions), "result": fin.get("data", {})}


# ================== FLASK APP ==================
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Hello from IOE Auto Solver on Railway!"})

@app.route("/run", methods=["POST"])
def run_task():
    data = request.json
    link = data.get("link", "")
    if not link:
        return jsonify({"error": "Missing link"}), 400
    result = run(link)
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
