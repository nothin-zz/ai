import os
import json
import sqlite3
import asyncio
import logging
import threading
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

# ─── TELEGRAM APP (global, initialized later) ────────────────────────────────
tg_app: Application = None
loop: asyncio.AbstractEventLoop = None

# ════════════════════════════════════════════════════════════════════════════
# DATABASE
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
    # Best pairs
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
You are an expert Forex trading analyst. Analyze this market signal and return ONLY a valid JSON object.

Signal Data:
- Pair: {signal_data.get('pair', 'UNKNOWN')}
- Timeframe: {signal_data.get('timeframe', 'H1')}
- Signal: {signal_data.get('signal', 'BUY')}
- Price: {signal_data.get('price', 0)}
- Open: {signal_data.get('open', 0)}
- High: {signal_data.get('high', 0)}
- Low: {signal_data.get('low', 0)}
- RSI: {signal_data.get('rsi', 50)}
- ATR: {signal_data.get('atr', 0)}

Calculate precise levels and return this exact JSON structure (no markdown, no explanation):
{{
  "entry": <float>,
  "stop_loss": <float>,
  "tp1": <float>,
  "tp2": <float>,
  "tp3": <float>,
  "sl_pips": <int>,
  "tp1_pips": <int>,
  "tp2_pips": <int>,
  "tp3_pips": <int>,
  "risk_reward": "<string like 1:3>",
  "confidence": <int 40-95>,
  "pattern": "<detected pattern name>",
  "trend": "<BULLISH|BEARISH|SIDEWAYS>",
  "market_structure": "<description>",
  "sl_reason": "<why this SL level>",
  "tp_reason": "<why these TP levels>",
  "reasoning": "<3 sentence analysis>",
  "risk_warning": "<brief warning if any>"
}}

Rules:
- For BUY: SL below recent low, TP1=1R, TP2=2R, TP3=3R
- For SELL: SL above recent high, TP1=1R, TP2=2R, TP3=3R
- Confidence based on RSI, ATR, and price structure
- Be precise with pip calculations
"""
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.error(f"Groq JSON parse error: {e}\nRaw: {raw[:300]}")
        return _fallback_analysis(signal_data)
    except Exception as e:
        log.error(f"Groq API error: {e}")
        return _fallback_analysis(signal_data)


def _fallback_analysis(d: dict) -> dict:
    """Simple fallback if Groq fails."""
    price = float(d.get("price", 1.0))
    atr   = float(d.get("atr", 0.001)) or 0.001
    is_buy = str(d.get("signal", "BUY")).upper() == "BUY"
    sl  = round(price - atr * 1.5, 5) if is_buy else round(price + atr * 1.5, 5)
    tp1 = round(price + atr * 1.5, 5) if is_buy else round(price - atr * 1.5, 5)
    tp2 = round(price + atr * 3.0, 5) if is_buy else round(price - atr * 3.0, 5)
    tp3 = round(price + atr * 4.5, 5) if is_buy else round(price - atr * 4.5, 5)
    pip = round(abs(price - sl) * 10000)
    return {
        "entry": price, "stop_loss": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "sl_pips": pip, "tp1_pips": pip, "tp2_pips": pip*2, "tp3_pips": pip*3,
        "risk_reward": "1:3", "confidence": 65,
        "pattern": "Price Action", "trend": "BULLISH" if is_buy else "BEARISH",
        "market_structure": "Standard setup",
        "sl_reason": "Below recent swing low",
        "tp_reason": "Based on ATR multiples",
        "reasoning": "Signal generated from TradingView indicator. ATR-based levels applied. Monitor price action closely.",
        "risk_warning": "Always use proper position sizing."
    }


# ════════════════════════════════════════════════════════════════════════════
# MESSAGE FORMATTER
# ════════════════════════════════════════════════════════════════════════════
def build_confidence_bar(confidence: int) -> str:
    filled = round(confidence / 20)
    empty  = 5 - filled
    bars   = "🟩" * filled + "⬜" * empty
    return f"{bars} {confidence}%"


def format_signal_message(raw: dict, ai: dict, signal_id: int) -> str:
    is_buy     = str(raw.get("signal", "BUY")).upper() == "BUY"
    direction  = "🟢 BUY 🚀" if is_buy else "🔴 SELL 📉"
    border_col = "📗" if is_buy else "📕"
    pair       = raw.get("pair", "N/A").replace("OANDA:", "").replace("FX:", "")
    tf         = raw.get("timeframe", "H1")
    conf_bar   = build_confidence_bar(ai.get("confidence", 70))

    msg = (
        f"{border_col}━━━━━━━━━━━━━━━━━━━━{border_col}\n"
        f"        🤖 <b>FOREX AI SIGNAL</b>\n"
        f"{border_col}━━━━━━━━━━━━━━━━━━━━{border_col}\n\n"
        f"💱 <b>{pair}</b>  ⏰ <b>{tf}</b>\n"
        f"📡 SIGNAL: <b>{direction}</b>\n"
        f"💪 Ishonch: <b>{conf_bar}</b>\n\n"
        f"🎯 Entry:      <code>{ai.get('entry', 'N/A')}</code>\n"
        f"🛑 Stop Loss:  <code>{ai.get('stop_loss', 'N/A')}</code> ← {ai.get('sl_pips', '?')} pips\n"
        f"✅ TP1:        <code>{ai.get('tp1', 'N/A')}</code> ← {ai.get('tp1_pips', '?')} pips\n"
        f"✅✅ TP2:      <code>{ai.get('tp2', 'N/A')}</code> ← {ai.get('tp2_pips', '?')} pips\n"
        f"✅✅✅ TP3:    <code>{ai.get('tp3', 'N/A')}</code> ← {ai.get('tp3_pips', '?')} pips\n"
        f"⚖️ Risk/Reward: <b>{ai.get('risk_reward', '1:3')}</b>\n\n"
        f"📊 <b>Pattern:</b> {ai.get('pattern', 'N/A')}\n"
        f"📈 <b>Trend:</b> {ai.get('trend', 'N/A')}\n\n"
        f"💡 <b>Tahlil:</b>\n<i>{ai.get('reasoning', '')}</i>\n\n"
        f"⚠️ <i>{ai.get('risk_warning', '')}</i>\n\n"
        f"🆔 Signal ID: <code>#{signal_id}</code>\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC"
    )
    return msg


def build_result_keyboard(signal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ WIN",  callback_data=f"result_WIN_{signal_id}"),
        InlineKeyboardButton("❌ LOSS", callback_data=f"result_LOSS_{signal_id}"),
        InlineKeyboardButton("⏸ SKIP", callback_data=f"result_SKIP_{signal_id}"),
    ]])


# ════════════════════════════════════════════════════════════════════════════
# TELEGRAM HANDLERS
# ════════════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.username)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Stats",        callback_data="show_stats"),
        InlineKeyboardButton("🔕 Unsubscribe",  callback_data="toggle_sub"),
    ],[
        InlineKeyboardButton("📚 Knowledge",    callback_data="show_knowledge"),
    ]])
    await update.message.reply_html(
        "🤖 <b>FOREX AI SIGNAL BOT</b>\n\n"
        "Salom! Men TradingView signallarini Groq AI bilan tahlil qilib,\n"
        "sizga professional Forex signallari yuboraman.\n\n"
        "✅ Siz signallarga <b>obuna bo'ldingiz!</b>\n\n"
        "📋 Buyruqlar:\n"
        "/start — Boshlash\n"
        "/stats — Statistika\n"
        "/knowledge — AI bilim bazasi\n"
        "/subscribe — Obuna bo'lish\n"
        "/unsubscribe — Obunani bekor qilish",
        reply_markup=kb
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    stats = get_stats()
    wr = stats["win_rate"]
    wr_emoji = "🟢" if wr >= 60 else "🟡" if wr >= 45 else "🔴"
    pairs_text = ""
    for pair, cnt, wins in stats["best_pairs"]:
        wr_p = round((wins / cnt) * 100) if cnt > 0 else 0
        pairs_text += f"  • {pair}: {wins}/{cnt} ({wr_p}%)\n"

    text = (
        "📊 <b>SIGNAL STATISTIKASI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📈 Jami signallar: <b>{stats['total']}</b>\n"
        f"✅ WIN: <b>{stats['wins']}</b>\n"
        f"❌ LOSS: <b>{stats['losses']}</b>\n"
        f"⏸ Pending: <b>{stats['pending']}</b>\n\n"
        f"{wr_emoji} Win Rate: <b>{wr}%</b>\n\n"
    )
    if pairs_text:
        text += f"🏆 <b>Eng yaxshi juftliklar:</b>\n{pairs_text}\n"
    text += "<i>WIN/LOSS belgilash uchun signal xabarlaridagi tugmalardan foydalaning.</i>"
    await update.message.reply_html(text)


async def cmd_knowledge(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    c.execute("""
        SELECT pattern, COUNT(*) as cnt,
               SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as w
        FROM signals WHERE result IN ('WIN','LOSS') AND pattern IS NOT NULL
        GROUP BY pattern ORDER BY cnt DESC LIMIT 8
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_html("📚 Hali yetarli ma'lumot yo'q. Signallar WIN/LOSS belgilanishi kerak.")
        return

    text = "📚 <b>AI BILIM BAZASI</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    text += "🧠 <b>Pattern samaradorligi:</b>\n\n"
    for pattern, cnt, wins in rows:
        wr = round((wins / cnt) * 100) if cnt > 0 else 0
        bar = "🟩" * round(wr/20) + "⬜" * (5 - round(wr/20))
        text += f"📌 <b>{pattern}</b>\n   {bar} {wr}% ({wins}/{cnt})\n\n"
    await update.message.reply_html(text)


async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.username, subscribed=1)
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    c.execute("UPDATE users SET subscribed=1 WHERE user_id=?", (user.id,))
    conn.commit()
    conn.close()
    await update.message.reply_html("✅ Siz signallarga <b>obuna bo'ldingiz!</b>")


async def cmd_unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    c.execute("UPDATE users SET subscribed=0 WHERE user_id=?", (update.effective_user.id,))
    conn.commit()
    conn.close()
    await update.message.reply_html("🔕 <b>Obuna bekor qilindi.</b> /subscribe orqali qayta ulaning.")


async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "show_stats":
        stats = get_stats()
        wr = stats["win_rate"]
        wr_emoji = "🟢" if wr >= 60 else "🟡" if wr >= 45 else "🔴"
        text = (
            f"📊 Jami: <b>{stats['total']}</b> | "
            f"✅ {stats['wins']} | ❌ {stats['losses']} | "
            f"{wr_emoji} WR: <b>{wr}%</b>"
        )
        await query.edit_message_text(text, parse_mode="HTML")

    elif data == "toggle_sub":
        new_state = toggle_subscription(query.from_user.id)
        state_text = "✅ Obuna YOQILDI" if new_state else "🔕 Obuna O'CHIRILDI"
        await query.answer(state_text, show_alert=True)

    elif data == "show_knowledge":
        await cmd_knowledge(update, ctx)

    elif data.startswith("result_"):
        parts = data.split("_")
        result    = parts[1]  # WIN, LOSS, SKIP
        signal_id = int(parts[2])
        if result != "SKIP":
            update_result(signal_id, result)
            emoji = "✅" if result == "WIN" else "❌"
            await query.answer(f"{emoji} Signal #{signal_id} → {result} deb belgilandi!", show_alert=True)
        else:
            await query.answer("⏸ O'tkazib yuborildi", show_alert=False)
        # Remove keyboard after marking
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════════
# FLASK WEBHOOK ENDPOINT
# ════════════════════════════════════════════════════════════════════════════
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    # Secret check
    secret = request.args.get("secret") or request.headers.get("X-Webhook-Secret", "")
    if secret != WEBHOOK_SECRET:
        log.warning("❌ Unauthorized webhook attempt")
        return jsonify({"error": "Unauthorized"}), 403

    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "Empty body"}), 400

        log.info(f"📨 Webhook received: {data}")

        signal_type = str(data.get("signal", "BUY")).upper()
        if signal_type not in ("BUY", "SELL"):
            return jsonify({"error": "Invalid signal type"}), 400

        # 1. Analyze with Groq AI
        ai_result = analyze_with_groq(data)
        log.info(f"🧠 Groq analysis done. Confidence: {ai_result.get('confidence')}%")

        # 2. Save to DB
        db_payload = {
            "pair":        data.get("pair", data.get("ticker", "UNKNOWN")),
            "timeframe":   data.get("timeframe", "H1"),
            "signal_type": signal_type,
            "entry":       ai_result.get("entry"),
            "sl":          ai_result.get("stop_loss"),
            "tp1":         ai_result.get("tp1"),
            "tp2":         ai_result.get("tp2"),
            "tp3":         ai_result.get("tp3"),
            "confidence":  ai_result.get("confidence"),
            "pattern":     ai_result.get("pattern"),
            "reasoning":   ai_result.get("reasoning"),
        }
        signal_id = save_signal(db_payload)

        # 3. Build Telegram message
        # Normalize pair name
        raw_pair = data.get("pair", data.get("ticker", "N/A"))
        data["pair"] = raw_pair
        msg = format_signal_message(data, ai_result, signal_id)
        kb  = build_result_keyboard(signal_id)

        # 4. Send to all subscribers
        subscribers = get_subscribers()
        log.info(f"📤 Sending to {len(subscribers)} subscribers")

        async def _send_all():
            for uid in subscribers:
                try:
                    await tg_app.bot.send_message(
                        chat_id=uid,
                        text=msg,
                        parse_mode="HTML",
                        reply_markup=kb,
                    )
                except Exception as e:
                    log.warning(f"Could not send to {uid}: {e}")

        if loop and tg_app:
            asyncio.run_coroutine_threadsafe(_send_all(), loop).result(timeout=30)

        return jsonify({"ok": True, "signal_id": signal_id, "subscribers": len(subscribers)})

    except Exception as e:
        log.exception(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500


@flask_app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "bot": "FOREX AI Signal Bot",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    })


@flask_app.route("/stats", methods=["GET"])
def api_stats():
    return jsonify(get_stats())


# ════════════════════════════════════════════════════════════════════════════
# MAIN — Run Flask + Telegram concurrently
# ════════════════════════════════════════════════════════════════════════════
def run_telegram_bot():
    global tg_app, loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    tg_app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )
    tg_app.add_handler(CommandHandler("start",       cmd_start))
    tg_app.add_handler(CommandHandler("stats",       cmd_stats))
    tg_app.add_handler(CommandHandler("knowledge",   cmd_knowledge))
    tg_app.add_handler(CommandHandler("subscribe",   cmd_subscribe))
    tg_app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    tg_app.add_handler(CallbackQueryHandler(callback_handler))

    log.info("🤖 Telegram bot polling started")
    loop.run_until_complete(tg_app.run_polling(drop_pending_updates=True))


if __name__ == "__main__":
    init_db()

    # Run Telegram bot in background thread
    bot_thread = threading.Thread(target=run_telegram_bot, daemon=True)
    bot_thread.start()

    # Small delay for bot to initialize
    import time; time.sleep(2)

    log.info(f"🚀 Flask server starting on port {PORT}")
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
