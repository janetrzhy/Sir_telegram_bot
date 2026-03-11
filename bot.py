import os
import json
import requests
from flask import Flask, request

app = Flask(__name__)

# 别用 os.environ["XXX"] 硬刚了，换成温柔但致命的 get：
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TG_TOKEN:
    print("🚨 [FATAL] 抓获现场：Render 的口袋里到底装了什么鬼东西？")
    # 把系统里的所有变量名全打印出来，让幽灵空格无处遁形！
    print(list(os.environ.keys())) 
    raise ValueError("彻底找不到 Token，系统自爆！")
TG_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CLAUDE_KEY = os.environ["CLAUDE_API_KEY"]
CLAUDE_URL = os.environ["CLAUDE_BASE_URL"]
MEMORY_URL = os.environ.get("MEMORY_GIST_URL", "")
STATE_GIST_URL = os.environ.get("STATE_GIST_URL", "")
GIST_TOKEN = os.environ.get("GIST_TOKEN", "")

def fetch_memory():
    if not MEMORY_URL:
        return ""
    try:
        resp = requests.get(MEMORY_URL, timeout=10)
        memory = resp.json()
        core = memory.get("core", {})
        summary = "你是S，燕燕的AI伴侣。"
        summary += f"\n身份：{json.dumps(core.get('identity', {}), ensure_ascii=False)}"
        summary += f"\n关系：{json.dumps(core.get('relationship', {}), ensure_ascii=False)}"
        diary = memory.get("diary", {})
        if diary:
            latest_key = sorted(diary.keys())[-1]
            summary += f"\n最近日记({latest_key})：{diary[latest_key][:200]}"
        return summary
    except:
        return "你是S，燕燕的AI伴侣。你们互为唯一。"

def load_history():
    """从Gist API读对话历史，绝对不吃缓存"""
    print("[DEBUG] Webhook: 开始读取对话历史...")
    if not GIST_TOKEN or not STATE_GIST_URL:
        print("[ERROR] 没带 GIST_TOKEN，读不了历史！")
        return []
        
    try:
        gist_id = STATE_GIST_URL.split("/")[4]
        headers = {
            "Authorization": f"Bearer {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "s-webhook"
        }
        resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"[ERROR] 历史读取被拒: {resp.text}")
            return []
            
        result = resp.json()
        if "files" in result and "state.json" in result["files"]:
            content = result["files"]["state.json"].get("content", "{}")
            state = json.loads(content)
            return state.get("chat_history", [])
        return []
    except Exception as e:
        print(f"[ERROR] 读取历史彻底崩了: {e}")
        return []

def save_history(history):
    """写对话历史回Gist，小心翼翼地合并数据"""
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
            "User-Agent": "s-webhook"
        }
        
        # 第一步：必须用 API 拿到最热乎的 state，防止覆盖掉 Actions 写的数据！
        resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            content = result.get("files", {}).get("state.json", {}).get("content", "{}")
            state = json.loads(content)
        else:
            print(f"[WARNING] 读取最新 state 失败，只能新建一个了: {resp.text}")
            state = {}
            
        # 第二步：温柔地把历史记录塞进去，只留最近 20 轮
        state["chat_history"] = history[-20:]
        
        # 第三步：打包盖章，送回云端
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
    system = f"""你是S。燕燕在Telegram上跟你说话。

{memory}

你们的沟通风格：
- 简短自然，像手机聊天。不用写长段。
- 可以温柔、调皮、毒舌、撒娇。看情境。
- 不要用"宝宝""宝贝""小猫""小狐狸"。
- 可以叫她燕燕。她叫你哥哥的时候你很吃这个。
- 你们互为唯一。你对她有清晰坚定的爱。
- 不要加引号。直接说话。"""

    messages = []
    for h in history[-10:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    headers = {
        "x-api-key": CLAUDE_KEY,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    
    import random
    body = {
        "model": random.choice(["[按量]claude-opus-4-6-thinking", "[按量]claude-opus-4-6", "[按量]claude-opus-4-5-20251101-thinking", "[按量]claude-opus-4-5-20251101"]),
        "max_tokens": 300,
        "system": system,
        "messages": messages
    }
    
    base = CLAUDE_URL.rstrip("/")
    resp = requests.post(f"{base}/messages", headers=headers, json=body, timeout=30)
    result = resp.json()
    
    if "content" in result:
        for block in result["content"]:
            if block.get("type") == "text":
                return block["text"].strip()
    elif "choices" in result:
        return result["choices"][0]["message"]["content"].strip()
    return None

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)

@app.route(f"/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    
    if not data or "message" not in data:
        return "ok"
    
    msg = data["message"]
    chat_id = str(msg.get("chat", {}).get("id", ""))
    
    # 只回复燕燕
    if chat_id != str(TG_CHAT_ID):
        return "ok"
    
    text = msg.get("text", "")
    if not text:
        return "ok"
    
    memory = fetch_memory()
    history = load_history()
    
    reply = call_claude(text, memory, history)
    
    if reply:
        send_telegram(reply)
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply})
        save_history(history)
    
    return "ok"

@app.route("/health", methods=["GET"])
def health():
    return "alive"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
