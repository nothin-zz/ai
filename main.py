import os
import json
import sqlite3
import asyncio
import logging
import threading
import re
from datetime import datetime
from flask import Flask, request, jsonify
from groq import Groq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ─── CONFIG ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "8776282635:AAExON8KZhR8w_ZfZthurcLb7LB2AsMuk9A")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "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")
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "secret")
PORT             = int(os.getenv("PORT", 8080))
GROQ_MODEL       = "llama-3.3-70b-versatile"

# ─── FLASK APP ───────────────────────────────────────────────────────────────
flask_app = Flask(__name__)

# ─── GROQ CLIENT ─────────────────────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)

# ─── GLOBAL STATES ──────────────────────────────────────────────────────────
tg_app: Application = None
main_loop: asyncio.AbstractEventLoop = None

# ════════════════════════════════════════════════════════════════════════════
# DATABASE FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pair        TEXT,
            timeframe   TEXT,
            signal_type TEXT,
            entry       REAL,
            sl          REAL,
            tp1         REAL,
            tp2         REAL,
            tp3         REAL,
            confidence  INTEGER,
            pattern     TEXT,
            reasoning   TEXT,
            result      TEXT DEFAULT 'PENDING',
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            subscribed  INTEGER DEFAULT 1,
            joined_at   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    log.info("✅ Database initialized")

def save_signal(data: dict) -> int:
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO signals
            (pair, timeframe, signal_type, entry, sl, tp1, tp2, tp3,
             confidence, pattern, reasoning)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data.get("pair"), data.get("timeframe"), data.get("signal_type"),
        data.get("entry"), data.get("sl"), data.get("tp1"),
        data.get("tp2"), data.get("tp3"), data.get("confidence"),
        data.get("pattern"), data.get("reasoning")
    ))
    signal_id = c.lastrowid
    conn.commit()
    conn.close()
    return signal_id

def update_result(signal_id: int, result: str):
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    c.execute("UPDATE signals SET result=? WHERE id=?", (result, signal_id))
    conn.commit()
    conn.close()

def get_stats() -> dict:
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM signals")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM signals WHERE result='WIN'")
    wins = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM signals WHERE result='LOSS'")
    losses = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM signals WHERE result='PENDING'")
    pending = c.fetchone()[0]
    c.execute("""
        SELECT pair, COUNT(*) as cnt,
               SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as w
        FROM signals WHERE result IN ('WIN','LOSS')
        GROUP BY pair ORDER BY w DESC LIMIT 3
    """)
    best_pairs = c.fetchall()
    conn.close()
    win_rate = round((wins / (wins + losses)) * 100, 1) if (wins + losses) > 0 else 0
    return {
        "total": total, "wins": wins, "losses": losses,
        "pending": pending, "win_rate": win_rate, "best_pairs": best_pairs
    }

def get_subscribers() -> list[int]:
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE subscribed=1")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def upsert_user(user_id: int, username: str, subscribed: int = 1):
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO users (user_id, username, subscribed)
        VALUES (?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username
    """, (user_id, username or "", subscribed))
    conn.commit()
    conn.close()

def toggle_subscription(user_id: int) -> bool:
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    c.execute("SELECT subscribed FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    new_state = 0 if (row and row[0] == 1) else 1
    c.execute("UPDATE users SET subscribed=? WHERE user_id=?", (new_state, user_id))
    conn.commit()
    conn.close()
    return bool(new_state)

# ════════════════════════════════════════════════════════════════════════════
# GROQ AI ANALYSIS
# ════════════════════════════════════════════════════════════════════════════
def analyze_with_groq(signal_data: dict) -> dict:
    prompt = f"""
You are an expert Forex analyst. Analyze and return ONLY a valid JSON object.
Data: {json.dumps(signal_data)}
Structure:
{{
  "entry": <float>, "stop_loss": <float>, "tp1": <float>, "tp2": <float>, "tp3": <float>,
  "sl_pips": <int>, "tp1_pips": <int>, "tp2_pips": <int>, "tp3_pips": <int>,
  "risk_reward": "1:3", "confidence": <int>, "pattern": "name", "trend": "BULLISH/BEARISH",
  "reasoning": "3 sentences max", "risk_warning": "warning"
}}
"""
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        content = response.choices[0].message.content.strip()
        # JSONni tozalab olish (Markdownlarsiz)
        json_str = re.search(r'\{.*\}', content, re.DOTALL).group()
        return json.loads(json_str)
    except Exception as e:
        log.error(f"Groq AI xatosi: {e}")
        return _fallback_analysis(signal_data)

def _fallback_analysis(d: dict) -> dict:
    price = float(d.get("price", 1.0))
    is_buy = str(d.get("signal", "BUY")).upper() == "BUY"
    offset = 0.0020 # 20 pips simple offset
    return {
        "entry": price, "stop_loss": round(price - offset if is_buy else price + offset, 5),
        "tp1": round(price + offset if is_buy else price - offset, 5),
        "tp2": round(price + offset*2 if is_buy else price - offset*2, 5),
        "tp3": round(price + offset*3 if is_buy else price - offset*3, 5),
        "sl_pips": 20, "tp1_pips": 20, "tp2_pips": 40, "tp3_pips": 60,
        "risk_reward": "1:3", "confidence": 60, "pattern": "Breakout", "trend": "Neutral",
        "reasoning": "Texnik tahlil asosida hisoblandi.", "risk_warning": "Sizga omad tilaymiz!"
    }

# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════
def format_signal_message(raw: dict, ai: dict, signal_id: int) -> str:
    is_buy = str(raw.get("signal", "BUY")).upper() == "BUY"
    direction = "🟢 BUY 🚀" if is_buy else "🔴 SELL 📉"
    pair = str(raw.get("pair", "N/A")).split(':')[-1]
    
    msg = (
        f"🤖 <b>FOREX AI SIGNAL #{signal_id}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💱 <b>{pair}</b> | ⏰ <b>{raw.get('timeframe', 'H1')}</b>\n"
        f"📡 SIGNAL: <b>{direction}</b>\n"
        f"💪 Ishonch: <b>{ai.get('confidence')}%</b>\n\n"
        f"🎯 Entry: <code>{ai.get('entry')}</code>\n"
        f"🛑 SL: <code>{ai.get('stop_loss')}</code> ({ai.get('sl_pips')} pips)\n"
        f"✅ TP1: <code>{ai.get('tp1')}</code>\n"
        f"✅ TP2: <code>{ai.get('tp2')}</code>\n"
        f"✅ TP3: <code>{ai.get('tp3')}</code>\n\n"
        f"📊 Pattern: {ai.get('pattern')}\n"
        f"💡 Tahlil: <i>{ai.get('reasoning')}</i>"
    )
    return msg

# ════════════════════════════════════════════════════════════════════════════
# TELEGRAM BOT LOGIC
# ════════════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user.id, update.effective_user.username)
    await update.message.reply_html("<b>Forex AI Signal Bot ishga tushdi!</b>\nTez orada signallarni qabul qilasiz.")

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("result_"):
        _, res, s_id = query.data.split("_")
        update_result(int(s_id), res)
        await query.edit_message_reply_markup(None)
        await query.message.reply_text(f"Signal #{s_id} -> {res} deb saqlandi.")

# ════════════════════════════════════════════════════════════════════════════
# WEBHOOK & FLASK
# ════════════════════════════════════════════════════════════════════════════
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    # Secret check
    if request.args.get("secret") != WEBHOOK_SECRET:
        return jsonify({"error": "Unauthorized"}), 403

    try:
        data = request.get_json(force=True)
        ai_res = analyze_with_groq(data)
        
        db_data = {
            "pair": data.get("pair", "UNKNOWN"),
            "timeframe": data.get("timeframe", "H1"),
            "signal_type": data.get("signal", "BUY"),
            "entry": ai_res.get("entry"),
            "sl": ai_res.get("stop_loss"),
            "tp1": ai_res.get("tp1"),
            "tp2": ai_res.get("tp2"),
            "tp3": ai_res.get("tp3"),
            "confidence": ai_res.get("confidence"),
            "pattern": ai_res.get("pattern"),
            "reasoning": ai_res.get("reasoning")
        }
        s_id = save_signal(db_data)
        
        msg = format_signal_message(data, ai_res, s_id)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ WIN", callback_data=f"result_WIN_{s_id}"),
            InlineKeyboardButton("❌ LOSS", callback_data=f"result_LOSS_{s_id}")
        ]])

        # ASYNC yuborish
        async def broadcast():
            subs = get_subscribers()
            for uid in subs:
                try:
                    await tg_app.bot.send_message(chat_id=uid, text=msg, parse_mode="HTML", reply_markup=kb)
                except Exception as e:
                    log.error(f"User {uid} xatosi: {e}")

        if main_loop and main_loop.is_running():
            asyncio.run_coroutine_threadsafe(broadcast(), main_loop)

        return jsonify({"status": "sent", "id": s_id})
    except Exception as e:
        log.exception("Webhook xatosi:")
        return jsonify({"error": str(e)}), 500

# ════════════════════════════════════════════════════════════════════════════
# RUNNER
# ════════════════════════════════════════════════════════════════════════════
def start_bot():
    global tg_app, main_loop
    main_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(main_loop)
    
    tg_app = Application.builder().token(TELEGRAM_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CallbackQueryHandler(callback_handler))
    
    log.info("🤖 Bot polling boshlandi...")
    tg_app.run_polling(drop_pending_updates=True, close_loop=False)

if __name__ == "__main__":
    init_db()
    # Botni alohida oqimda ishga tushirish
    threading.Thread(target=start_bot, daemon=True).start()
    
    # Flaskni ishga tushirish
    log.info(f"🚀 Server {PORT} portda ishlamoqda...")
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

