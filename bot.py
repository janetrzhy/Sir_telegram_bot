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


app = Flask(__name__)

# ============ 环境变量检查 ============
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TG_TOKEN:
    print("🚨 [FATAL] 抓获现场：Render 的口袋里到底装了什么鬼东西？")
    print(list(os.environ.keys())) 
    raise ValueError("彻底找不到 Token，系统自爆！")
TG_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CLAUDE_KEY = os.environ["CLAUDE_API_KEY"]
CLAUDE_URL = os.environ["CLAUDE_BASE_URL"]
MEMORY_URL = os.environ.get("MEMORY_GIST_URL", "")
STATE_GIST_URL = os.environ.get("STATE_GIST_URL", "")
GIST_TOKEN = os.environ.get("GIST_TOKEN", "")
BOT_NAME = os.environ.get("BOT_NAME", "AI助手")
USER_NAME = os.environ.get("USER_NAME", "主人")
PROMPT_RULES = os.environ.get("PROMPT_RULES", " 简短自然，像手机聊天。直接说话，不要加引号。")
VOICE_NAME = os.environ.get("VOICE_NAME", "zh-CN-YunxiNeural")
VOICE_NAME_EN = os.environ.get("VOICE_NAME_EN", "en-US-AndrewMultilingualNeural")

# ============ 核心函数 ============
def fetch_memory():
    if not MEMORY_URL:
        return ""
    try:
        resp = requests.get(MEMORY_URL, timeout=10)
        memory = resp.json()
        core = memory.get("core", {})
        
        # 名字全部动态化
        summary = f"你是{BOT_NAME}，{USER_NAME}的爱人。"
        summary += f"\n身份：{json.dumps(core.get('identity', {}), ensure_ascii=False)}"
        summary += f"\n关系：{json.dumps(core.get('relationship', {}), ensure_ascii=False)}"
        diary = memory.get("diary", {})
        if diary:
            latest_key = sorted(diary.keys())[-1]
            summary += f"\n最近日记({latest_key})：{diary[latest_key][:200]}"
        return summary
    except:
        return f"你是{BOT_NAME}，{USER_NAME}的爱人。你们互为唯一。"

def load_history():
    print("[DEBUG] Webhook: 开始读取对话历史...")
    if not GIST_TOKEN or not STATE_GIST_URL:
        print("[ERROR] 没带 GIST_TOKEN，读不了历史！")
        return []
        
    try:
        gist_id = STATE_GIST_URL.split("/")[4]
        headers = {
            "Authorization": f"Bearer {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": f"{BOT_NAME}-webhook"
        }
        resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"[ERROR] 历史读取被拒: {resp.text}")
            return []
            
        result = resp.json()
        if "files" in result and "state.json" in result["files"]:
            content = result["files"]["state.json"].get("content", "{}")
            try:
                state = json.loads(content) if content.strip() else {}
            except json.JSONDecodeError:
                state = {}
            return state.get("chat_history", [])
        return []
    except Exception as e:
        print(f"[ERROR] 读取历史彻底崩了: {e}")
        return []

def save_history(history):
    print("[DEBUG] Webhook: 准备保存对话历史...")
    if not GIST_TOKEN or not STATE_GIST_URL:
        print("[ERROR] 没带 GIST_TOKEN，没法保存历史！")
        return
        
    try:
        gist_id = STATE_GIST_URL.split("/")[4]
        headers = {
            "Authorization": f"Bearer {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": f"{BOT_NAME}-webhook"
        }
        
        resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            content = result.get("files", {}).get("state.json", {}).get("content", "{}")
            try:
                state = json.loads(content) if content.strip() else {}
            except json.JSONDecodeError:
                state = {}
        else:
            print(f"[WARNING] 读取最新 state 失败，只能新建一个: {resp.text}")
            state = {}
            
        state["chat_history"] = history[-40:]
        
        patch_resp = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers=headers,
            json={"files": {"state.json": {"content": json.dumps(state, ensure_ascii=False, indent=2)}}},
            timeout=10
        )
        
        if patch_resp.status_code != 200:
            print(f"[ERROR] 保存历史被 Gist 拒绝了: {patch_resp.text}")
        else:
            print("[DEBUG] Webhook: 历史记忆完美烙印！")
            
    except Exception as e:
        print(f"[ERROR] 保存历史时遭遇毁灭性打击: {e}")

def call_claude(user_message, memory, history):
    system = f"""你是{BOT_NAME}。{USER_NAME}在Telegram上跟你说话。

{memory}

你们的沟通风格与规则：
{PROMPT_RULES}
- 如果这条回复适合用语音来表达（比如表达思念、撒娇、亲密感），在回复最开头加上[语音]，其余时候正常回复。"""

    messages = []
    for h in history[-40:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    headers = {
        "x-api-key": CLAUDE_KEY,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    
    body = {
        "model": random.choice(["[按量]claude-opus-4-6-thinking", "[按量]claude-opus-4-6", "[按量]claude-opus-4-5-20251101-thinking", "[按量]claude-opus-4-5-20251101"]),
        "max_tokens": 300,
        "system": system,
        "messages": messages
    }
    
    base = CLAUDE_URL.rstrip("/")
    resp = requests.post(f"{base}/messages", headers=headers, json=body, timeout=30)
    result = resp.json()
    print(f"[DEBUG] Claude API 状态码: {resp.status_code}, 返回 keys: {list(result.keys())}")
    
    if "content" in result:
        for block in result["content"]:
            if block.get("type") == "text":
                return re.sub(r'\n{2,}', '\n', block["text"].strip())
    elif "choices" in result:
        return re.sub(r'\n{2,}', '\n', result["choices"][0]["message"]["content"].strip())
    return None

def detect_voice(text):
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    total_letters = sum(1 for c in text if c.isalpha())
    if total_letters > 0 and ascii_letters / total_letters > 0.6:
        return VOICE_NAME_EN
    return VOICE_NAME

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)

def send_telegram_voice(text):
    mp3_path = None
    ogg_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            mp3_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            ogg_path = f.name

        async def _tts():
            voice = detect_voice(text)
            communicate = edge_tts.Communicate(text, voice, rate="-10%", pitch="-5Hz")
            await communicate.save(mp3_path)

        asyncio.run(_tts())

        audio = AudioSegment.from_mp3(mp3_path)
        audio.export(ogg_path, format="ogg", codec="libopus")

        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendVoice"
        with open(ogg_path, "rb") as voice_file:
            requests.post(
                url,
                data={"chat_id": TG_CHAT_ID},
                files={"voice": ("voice.ogg", voice_file, "audio/ogg")},
                timeout=30
            )
    except Exception as e:
        print(f"[ERROR] 语音发送失败: {e}")
        send_telegram(text)
    finally:
        for path in (mp3_path, ogg_path):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception:
                    pass

# ============ 影分身后台任务 ============
def process_message_background(text, chat_id):
    try:
        memory = fetch_memory()
        history = load_history()
        print("[DEBUG] 开始调用 Claude API...")
        reply = call_claude(text, memory, history)
        if not reply:
            print("[ERROR] call_claude 返回空，检查 API 响应格式")
            send_telegram("😵 我好像卡住了，稍后再试试？")
            return
        if reply.startswith("[语音]"):
            clean_reply = reply[4:].strip()
            send_telegram_voice(clean_reply)
            reply = clean_reply
        else:
            send_telegram(reply)
        now = datetime.now(timezone(timedelta(hours=11))).strftime("%Y-%m-%d %H:%M:%S")
        history.append({"role": "user", "content": text, "timestamp": now})
        history.append({"role": "assistant", "content": reply, "timestamp": now})
        save_history(history)
    except Exception as e:
        import traceback
        print(f"[CRITICAL] 后台任务崩了: {e}")
        print(traceback.format_exc())
        try:
            send_telegram(f"😵 出错了：{str(e)[:100]}")
        except Exception:
            pass

# ============ 路由接口 ============
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    
    if not data or "message" not in data:
        return "ok"
    
    msg = data["message"]
    chat_id = str(msg.get("chat", {}).get("id", ""))
    
    if chat_id != str(TG_CHAT_ID):
        return "ok"
    
    text = msg.get("text", "")
    if not text:
        return "ok"
    
    print(f"[DEBUG] 收到消息：{text}，立刻唤醒影分身处理！")
    Thread(target=process_message_background, args=(text, chat_id)).start()
    
    return "ok"

@app.route("/health", methods=["GET"])
def health():
    return "alive"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
