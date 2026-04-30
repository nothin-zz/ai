# 🤖 FOREX AI SIGNAL BOT

**TradingView → Railway.app (Python + Groq AI) → Telegram**

---

## 📁 FAYL TUZILMASI

```
forex-ai-bot/
├── main.py           ← Flask + Telegram Bot + Groq AI
├── requirements.txt  ← Python kutubxonalari
├── Procfile          ← Railway ishga tushirish
├── railway.json      ← Railway konfiguratsiya
├── strategy.pine     ← TradingView Pine Script v5
└── README.md
```

---

## 🚀 BOSQICHMA-BOSQICH O'RNATISH

### 1️⃣ TELEGRAM BOT YARATISH
1. [@BotFather](https://t.me/BotFather) ga yozing
2. `/newbot` → nom bering → `@username_bot`
3. **Token** ni saqlang: `123456:ABC-DEF...`

### 2️⃣ GROQ API KEY OLISH
1. [console.groq.com](https://console.groq.com) ga kiring
2. **API Keys** → **Create API Key**
3. Key ni saqlang: `gsk_...`

### 3️⃣ RAILWAY.APP GA DEPLOY

```bash
# 1. railway.app ga kiring va GitHub repo ulang
# YOKI Railway CLI:
npm install -g @railway/cli
railway login
railway new
railway up
```

**Environment Variables (Railway Dashboard → Variables):**
```
TELEGRAM_TOKEN   = 123456:ABC-DEF...
GROQ_API_KEY     = gsk_xxxxxxxxxxxx
WEBHOOK_SECRET   = mysecret123
PORT             = 8080
```

### 4️⃣ RAILWAY URL OLISH
Deploy bo'lgach: `https://your-app.railway.app`

### 5️⃣ TELEGRAM BOTNI ISHGA TUSHIRISH
1. Botingizga `/start` yuboring
2. **User ID** ni ko'rasiz — signallar shu IDga yuboriladi

---

## 📡 TRADINGVIEW ALERT SOZLASH

### Pine Script qo'shish:
1. TradingView → Chart → Pine Editor
2. `strategy.pine` mazmunini joylashtiring
3. **Add to Chart** bosing

### Alert yaratish (BUY uchun):
1. Chart ustida ⏰ belgisi → **Create Alert**
2. **Condition**: `🤖 FOREX AI SIGNAL — PRO` → `BUY Signal`
3. **Webhook URL**:
   ```
   https://your-app.railway.app/webhook?secret=mysecret123
   ```
4. **Message** maydoniga:
   ```json
   {"signal":"BUY","pair":"{{ticker}}","timeframe":"{{interval}}","price":"{{close}}","high":"{{high}}","low":"{{low}}","open":"{{open}}","rsi":"{{plot("RSI")}}","atr":"{{plot("ATR")}}","time":"{{timenow}}"}
   ```
5. **Save** bosing

### Alert yaratish (SELL uchun):
Xuddi shunday, lekin:
- Condition: `SELL Signal`
- Message'da `"signal":"SELL"`

---

## 🤖 TELEGRAM BOT BUYRUQLARI

| Buyruq | Tavsif |
|--------|--------|
| `/start` | Botni ishga tushirish va obuna bo'lish |
| `/stats` | WIN/LOSS statistikasi |
| `/knowledge` | Pattern samaradorligi |
| `/subscribe` | Signallarga obuna bo'lish |
| `/unsubscribe` | Obunani bekor qilish |

---

## 📊 SIGNAL FORMATI (Telegram)

```
📗━━━━━━━━━━━━━━━━━━━━📗
        🤖 FOREX AI SIGNAL
📗━━━━━━━━━━━━━━━━━━━━📗

💱 EURUSD  ⏰ H1
📡 SIGNAL: 🟢 BUY 🚀
💪 Ishonch: 🟩🟩🟩🟩⬜ 82%

🎯 Entry:      1.08450
🛑 Stop Loss:  1.08200 ← 25 pips
✅ TP1:        1.08700 ← 25 pips
✅✅ TP2:      1.08950 ← 50 pips
✅✅✅ TP3:    1.09200 ← 75 pips
⚖️ Risk/Reward: 1:3

📊 Pattern: EMA Crossover + Support Bounce
📈 Trend: BULLISH

💡 Tahlil:
Price bounced from key support with bullish EMA alignment...

🆔 Signal ID: #42
```

---

## 🧪 TEST QILISH

```bash
# Webhook test (lokal yoki Railway URL):
curl -X POST "https://your-app.railway.app/webhook?secret=mysecret123" \
  -H "Content-Type: application/json" \
  -d '{
    "signal": "BUY",
    "pair": "EURUSD",
    "timeframe": "H1",
    "price": "1.08450",
    "high": "1.08520",
    "low": "1.08380",
    "open": "1.08400",
    "rsi": "52.3",
    "atr": "0.00180",
    "time": "2024-01-15T10:30:00"
  }'
```

---

## ⚠️ MUHIM ESLATMALAR

- Bu bot **faqat ma'lumot uchun** — moliyaviy maslahat emas
- Har doim **risk management** qiling
- Real pul bilan ishlatishdan oldin demo account'da sinab ko'ring
- Groq API **bepul** lekin rate limit bor (ayda 500K token)

---

## 🔧 MUAMMOLARNI HAL QILISH

**Bot javob bermayapti:**
- Railway logsni tekshiring: `railway logs`
- Environment variables to'g'ri kiritilganini tekshiring

**Webhook kelmayapti:**
- TradingView Pro+ kerak (webhook uchun)
- URL va secret to'g'riligini tekshiring

**Groq xato:**
- API key tekshiring
- Rate limit bo'lishi mumkin, kutib ko'ring
