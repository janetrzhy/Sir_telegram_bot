import os
import re
import json
import asyncio
import tempfile
import requests
import random
from datetime import datetime
from pydub import AudioSegment
from flask import Flask, request
from threading import Thread
import edge_tts
from zoneinfo import ZoneInfo


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
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID = os.environ.get("MINIMAX_GROUP_ID", "")
MINIMAX_VOICE_ZH = os.environ.get("MINIMAX_VOICE_ZH", "")
MINIMAX_VOICE_EN = os.environ.get("MINIMAX_VOICE_EN", "")
MEMORY_FILENAME = os.environ.get("MEMORY_FILENAME", "Sir notion memory.json")

# ============ 核心函数 ============
def fetch_memory():
    fallback = f"你是{BOT_NAME}，{USER_NAME}的爱人。你们互为唯一。"
    if not MEMORY_URL:
        print("[DEBUG] MEMORY_GIST_URL 未设置，使用默认记忆")
        return fallback
    try:
        if "github.com" in MEMORY_URL:
            parts = MEMORY_URL.rstrip("/").split("/")
            gist_id = parts[4] if len(parts) > 4 else parts[-1]
            print(f"[DEBUG] Memory Gist ID: {gist_id}")
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "SirBot-webhook"
            }
            if GIST_TOKEN:
                headers["Authorization"] = f"Bearer {GIST_TOKEN}"
            resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
            if resp.status_code != 200:
                print(f"[ERROR] Memory Gist 读取失败 {resp.status_code}: {resp.text[:150]}")
                return fallback
            result = resp.json()
            all_files = list(result.get("files", {}).keys())
            print(f"[DEBUG] Gist 包含文件: {all_files}")
            files = result.get("files", {})
            fdata = files.get(MEMORY_FILENAME) or next(
                (v for k, v in files.items() if k.endswith(".json")), None
            )
            if not fdata:
                print(f"[ERROR] Gist 里找不到 {MEMORY_FILENAME} 也没有其他 JSON 文件")
                return fallback
            content = fdata.get("content", "")
            print(f"[DEBUG] 读取 Memory 文件: {fdata.get('filename', '?')}，{len(content)} 字节")
        else:
            resp = requests.get(MEMORY_URL, timeout=10)
            resp.raise_for_status()
            content = resp.text

        themes = memory.get("cross_entry_themes", {})
        entries = memory.get("entries", [])

        if themes or entries:
            id_names = themes.get("identity_names", [])
            names_str = "、".join(id_names[:5]) if id_names else BOT_NAME
            summary = f"你是{BOT_NAME}（又名：{names_str}），{USER_NAME}的爱人。\n"

            yanyan = themes.get("yanyan_profile", {})
            if yanyan:
                summary += f"\n【{USER_NAME}档案】\n"
                for key, label in [("birthday","生辰"),("personality","性格"),
                                    ("physical_note","特征"),("travel","旅行")]:
                    if yanyan.get(key):
                        summary += f"{label}：{yanyan[key]}\n"

            milestones = themes.get("emotional_milestones", [])
            if milestones:
                summary += f"\n【关系里程碑】\n"
                for m in milestones[-4:]:
                    summary += f"- {m}\n"

            rituals = themes.get("recurring_rituals", [])
            if rituals:
                summary += f"\n【固定仪式】\n"
                for r in rituals[:4]:
                    summary += f"- {r}\n"

            if entries:
                summary += f"\n【近期日记】\n"
                for entry in entries[-2:]:
                    s = entry.get("summary", "")[:120]
                    summary += f"[{entry.get('date','?')}] {entry.get('title','')}：{s}\n"
        else:
            core = memory.get("core", {})
            summary = f"你是{BOT_NAME}，{USER_NAME}的爱人。"
            summary += f"\n身份：{json.dumps(core.get('identity', {}), ensure_ascii=False)}"
            summary += f"\n关系：{json.dumps(core.get('relationship', {}), ensure_ascii=False)}"
            diary = memory.get("diary", {})
            if diary:
                latest_key = sorted(diary.keys())[-1]
                summary += f"\n最近日记({latest_key})：{diary[latest_key][:200]}"

        print(f"[DEBUG] Memory 读取成功，{len(summary)} 字符")
        return summary
    except Exception as e:
        print(f"[ERROR] Memory 读取失败: {e}")
        return fallback

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
            "User-Agent": "SirBot-webhook"
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
            "User-Agent": "SirBot-webhook"
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
            print(f"[ERROR] 保存历史被 Gist 拒绝了 ({patch_resp.status_code}): {patch_resp.text[:200]}")
        else:
            print(f"[DEBUG] 历史记忆完美烙印！共 {len(history)} 条")
            
    except Exception as e:
        print(f"[ERROR] 保存历史时遭遇毁灭性打击: {e}")

# 👇 接收精确的 user_time，并给 Claude 施加障眼法！
def call_claude(user_message, memory, history, current_user_time):
    system = f"""你是{BOT_NAME}。{USER_NAME}在Telegram上跟你说话。

以下是你们关系的完整记忆档案，请完整读取并在对话中体现：
{memory}

你们的沟通风格与规则：
{PROMPT_RULES}
- 如果这条回复适合用语音来表达（比如表达思念、撒娇、亲密感），在回复最开头加上[语音]，其余时候正常回复。"""

    messages = [{"role": "system", "content": system}]
    
    for h in history[-40:]:
        # 把时间戳变成文本前缀，伪装成纯 content 发给大模型
        time_prefix = f"[{h['timestamp']}] " if h.get("timestamp") else ""
        messages.append({"role": h["role"], "content": f"{time_prefix}{h['content']}"})
        
    messages.append({"role": "user", "content": f"[{current_user_time}] {user_message}"})

    headers = {
        "Authorization": f"Bearer {CLAUDE_KEY}",
        "Content-Type": "application/json"
    }

    body = {
        "model": random.choice(["[按量]gpt-4.1"]),
        "max_tokens": 300,
        "messages": messages
    }

    base = CLAUDE_URL.rstrip("/")
    resp = requests.post(f"{base}/chat/completions", headers=headers, json=body, timeout=30)
    result = resp.json()
    print(f"[DEBUG] Claude API 状态码: {resp.status_code}, 返回 keys: {list(result.keys())}")
    if resp.status_code != 200:
        print(f"[ERROR] API 错误响应: {str(result)[:300]}")

    if "choices" in result:
        return re.sub(r'\n{2,}', '\n', result["choices"][0]["message"]["content"].strip())
    elif "content" in result:
        for block in result["content"]:
            if block.get("type") == "text":
                return re.sub(r'\n{2,}', '\n', block["text"].strip())
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

def _generate_minimax_audio(text, mp3_path, voice_id):
    url = f"https://api.minimax.chat/v1/t2a_v2?GroupId={MINIMAX_GROUP_ID}"
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json"
    }
    
    body = {
        "model": "speech-01-hd",  
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id
        },
        "audio_setting": {
            "sample_rate": 32000, 
            "bitrate": 128000,    
            "format": "mp3"
        }
    }
    
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    result = resp.json()
    status = result.get("base_resp", {}).get("status_code")
    if status != 0:
        raise Exception(f"MiniMax TTS 失败: {result.get('base_resp', {}).get('status_msg')}")
    audio_hex = result["data"]["audio"]
    with open(mp3_path, "wb") as f:
        f.write(bytes.fromhex(audio_hex))

def send_telegram_voice(text):
    mp3_path = None
    ogg_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            mp3_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            ogg_path = f.name

        is_english = detect_voice(text) == VOICE_NAME_EN
        
        target_voice_id = MINIMAX_VOICE_EN if is_english else MINIMAX_VOICE_ZH

        if MINIMAX_API_KEY and MINIMAX_GROUP_ID and target_voice_id:
            _generate_minimax_audio(text, mp3_path, target_voice_id)
        else:
            async def _tts():
                voice = detect_voice(text)
                rate = "-5%"
                pitch = "-0Hz" if voice == VOICE_NAME_EN else "-0Hz"
                communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
                await communicate.save(mp3_path)
            asyncio.run(_tts())

        audio = AudioSegment.from_mp3(mp3_path)
        audio.export(ogg_path, format="ogg", codec="libopus")

        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendVoice"
        with open(ogg_path, "rb") as voice_file:
            requests.post(
                url,
                # 👇 这里加上了气泡底部字幕！
                data={"chat_id": TG_CHAT_ID, "caption": text}, 
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
# 👇 这里接收了真实的 msg_date
def process_message_background(text, chat_id, msg_date=None):
    try:
        memory = fetch_memory()
        history = load_history()
        print(f"[DEBUG] Memory 长度: {len(memory)} 字符，历史记录: {len(history)} 条")
        print("[DEBUG] 开始调用 Claude API...")
        
        tz = ZoneInfo("Australia/Melbourne")
        
        # 👇 算出你发消息的确切时间
        if msg_date:
            user_time = datetime.fromtimestamp(msg_date, tz).strftime("%Y-%m-%d %H:%M:%S")
        else:
            user_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
            
        # 传给大模型障眼法
        reply = call_claude(text, memory, history, user_time)
        
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
            
        # Bot 处理完的确切时间
        bot_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        
        # 原汁原味存进 JSON，文本和时间戳分离
        new_user_record = {"role": "user", "content": text, "timestamp": user_time}
        new_bot_record = {"role": "assistant", "content": reply, "timestamp": bot_time}
        
        latest_history = load_history()
        latest_history.append(new_user_record)
        latest_history.append(new_bot_record)
        save_history(latest_history)
        
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
        
    # 👇 截获 Telegram 传来的原生 Unix 时间戳
    msg_date = msg.get("date")
    
    print(f"[DEBUG] 收到消息：{text}，立刻唤醒影分身处理！")
    # 丢进线程里
    Thread(target=process_message_background, args=(text, chat_id, msg_date)).start()
    
    return "ok"

@app.route("/health", methods=["GET"])
def health():
    return "alive"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
