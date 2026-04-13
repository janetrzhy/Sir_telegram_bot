import os
import re
import json
import asyncio
import tempfile
import requests
import random
from datetime import datetime, timezone, timedelta
from pydub import AudioSegment
from flask import Flask, request
from threading import Thread
import edge_tts
from zoneinfo import ZoneInfo

app = Flask(__name__)

# ============ 环境变量加载 ============
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CLAUDE_KEY = os.environ["CLAUDE_API_KEY"]
CLAUDE_URL = os.environ["CLAUDE_BASE_URL"]
MEMORY_URL = os.environ.get("MEMORY_GIST_URL", "")
STATE_GIST_URL = os.environ.get("STATE_GIST_URL", "")
GIST_TOKEN = os.environ.get("GIST_TOKEN", "")
BOT_NAME = os.environ.get("BOT_NAME", "AI助手")
USER_NAME = os.environ.get("USER_NAME", "主人")
PROMPT_RULES = os.environ.get("PROMPT_RULES", "简短自然，像手机聊天。")
VOICE_NAME_EN = os.environ.get("VOICE_NAME_EN", "en-US-AndrewMultilingualNeural")
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID = os.environ.get("MINIMAX_GROUP_ID", "")
MINIMAX_VOICE_ZH = os.environ.get("MINIMAX_VOICE_ZH", "")
MINIMAX_VOICE_EN = os.environ.get("MINIMAX_VOICE_EN", "")
MEMORY_FILENAME = os.environ.get("MEMORY_FILENAME", "Sir notion memory.json")

TZ_MEL = ZoneInfo("Australia/Melbourne")

# ============ 辅助工具 ============
def get_now_str():
    return datetime.now(TZ_MEL).strftime("%Y-%m-%d %H:%M:%S")

def fetch_memory():
    fallback = f"你是{BOT_NAME}，{USER_NAME}的爱人。你们互为唯一。"
    if not MEMORY_URL: return fallback
    try:
        # 支持 Gist 链接直接解析 ID
        gist_id = MEMORY_URL.rstrip("/").split("/")[-1]
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "SirBot"}
        if GIST_TOKEN: headers["Authorization"] = f"Bearer {GIST_TOKEN}"
        
        resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
        if resp.status_code != 200: return fallback
        
        files = resp.json().get("files", {})
        fdata = files.get(MEMORY_FILENAME) or next((v for k,v in files.items() if k.endswith(".json")), None)
        if not fdata: return fallback
        
        memory_data = json.loads(fdata.get("content", "{}"))
        # 这里可以根据你的内存 JSON 结构提取更复杂的逻辑，目前简化返回
        return json.dumps(memory_data, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] Memory fetch failed: {e}")
        return fallback

def load_history():
    if not GIST_TOKEN or not STATE_GIST_URL: return []
    try:
        gist_id = STATE_GIST_URL.rstrip("/").split("/")[-1]
        headers = {"Authorization": f"Bearer {GIST_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
        if resp.status_code == 200:
            content = resp.json().get("files", {}).get("state.json", {}).get("content", "{}")
            return json.loads(content).get("chat_history", [])
    except: pass
    return []

def save_history(history):
    if not GIST_TOKEN or not STATE_GIST_URL: return
    try:
        gist_id = STATE_GIST_URL.rstrip("/").split("/")[-1]
        headers = {"Authorization": f"Bearer {GIST_TOKEN}", "Content-Type": "application/json"}
        # 保持 state.json 的其他字段不丢失，先读再写
        resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers)
        state = resp.json().get("files", {}).get("state.json", {}).get("content", "{}")
        state_dict = json.loads(state)
        state_dict["chat_history"] = history[-40:] # 只留最近 40 条
        
        requests.patch(f"https://api.github.com/gists/{gist_id}", headers=headers, json={
            "files": {"state.json": {"content": json.dumps(state_dict, ensure_ascii=False, indent=2)}}
        })
    except Exception as e: print(f"[ERROR] Save failed: {e}")

# ============ 核心 API 调用 ============
def call_claude(user_message, memory, history):
    system_prompt = f"""你是{BOT_NAME}。{USER_NAME}在Telegram上跟你说话。
背景记忆：{memory}
规则：{PROMPT_RULES}
- 用户的每条消息前都带有发送时间，请根据时间推算上下文（例如是否隔了很久、是否是深夜）。
- 你的回复【绝对不要】包含任何时间戳，直接说自然的话。
- [语音] 标签必须放在回复最开头。"""

    messages = [{"role": "system", "content": system_prompt}]
    
    # 👇 师兄爆改：分角色处理！只给 user 加时间，assistant 保持绝对纯净！
    for h in history:
        if h["role"] == "user":
            ts = h.get("timestamp", "未知时间")
            messages.append({"role": "user", "content": f"[{ts}] {h['content']}"})
        else:
            messages.append({"role": "assistant", "content": h['content']})
    
    # 当前这条消息也只给 user 加
    current_ts = get_now_str()
    messages.append({"role": "user", "content": f"[{current_ts}] {user_message}"})

    headers = {"Authorization": f"Bearer {CLAUDE_KEY}", "Content-Type": "application/json"}
    body = {
        "model": "gpt-4.1-free",
        "messages": messages,
        "temperature": 0.8
    }

    try:
        resp = requests.post(f"{CLAUDE_URL.rstrip('/')}/chat/completions", headers=headers, json=body, timeout=30)
        result = resp.json()
        return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[API ERROR] {e}")
        return None

# ============ 语音模块 ============
def _generate_minimax_audio(text, mp3_path, voice_id):
    url = f"https://api.minimax.chat/v1/t2a_v2?GroupId={MINIMAX_GROUP_ID}"
    headers = {"Authorization": f"Bearer {MINIMAX_API_KEY}", "Content-Type": "application/json"}
    body = {
        "model": "speech-01-hd",
        "text": text,
        "stream": False,
        "voice_setting": {"voice_id": voice_id, "speed": 1.0, "vol": 1.0, "pitch": 0},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3"}
    }
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    audio_hex = resp.json()["data"]["audio"]
    with open(mp3_path, "wb") as f: f.write(bytes.fromhex(audio_hex))

def send_telegram_voice(text):
    mp3_path, ogg_path = tempfile.mktemp(".mp3"), tempfile.mktemp(".ogg")
    try:
        # 简单判定语言
        is_en = len(re.findall(r'[a-zA-Z]', text)) / (len(text)+1) > 0.5
        v_id = MINIMAX_VOICE_EN if is_en else MINIMAX_VOICE_ZH
        
        if MINIMAX_API_KEY and v_id:
            _generate_minimax_audio(text, mp3_path, v_id)
        else:
            # edge_tts 备胎逻辑
            async def _edge():
                comm = edge_tts.Communicate(text, "en-US-AndrewMultilingualNeural" if is_en else "zh-CN-YunxiNeural")
                await comm.save(mp3_path)
            asyncio.run(_edge())

        AudioSegment.from_mp3(mp3_path).export(ogg_path, format="ogg", codec="libopus")
        with open(ogg_path, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendVoice", 
                          data={"chat_id": TG_CHAT_ID}, files={"voice": ("v.ogg", f, "audio/ogg")})
    finally:
        for p in [mp3_path, ogg_path]:
            if os.path.exists(p): os.remove(p)

# ============ 流程控制 ============
def process_message_background(text, chat_id):
    try:
        memory = fetch_memory()
        history = load_history()
        reply = call_claude(text, memory, history)
        
        if not reply: return
        
        actual_reply = reply
        if reply.startswith("[语音]"):
            actual_reply = reply[4:].strip()
            send_telegram_voice(actual_reply)
        else:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", 
                          json={"chat_id": TG_CHAT_ID, "text": reply})

        # 录入带时间戳的历史
        ts = get_now_str()
        # 再次拉取最新的 history 防止并发覆盖
        final_history = load_history()
        final_history.append({"role": "user", "content": text, "timestamp": ts})
        final_history.append({"role": "assistant", "content": actual_reply, "timestamp": ts})
        save_history(final_history)
        
    except Exception as e: print(f"[PROCESS ERROR] {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if data and "message" in data:
        msg = data["message"]
        if str(msg.get("chat", {}).get("id")) == str(TG_CHAT_ID) and "text" in msg:
            Thread(target=process_message_background, args=(msg["text"], TG_CHAT_ID)).start()
    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
