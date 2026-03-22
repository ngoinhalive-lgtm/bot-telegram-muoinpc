import requests
import time
import threading
from flask import Flask

# ==========================================
# PHẦN 1: CÀI ĐẶT THÔNG TIN CỦA BẠN
# ==========================================
TOKEN = '8700047218:AAHINxefZHAm_fGMEd3sPJMilNtYH36oSy0'
CHAT_ID = '7366887130'

VOLUME_MULTIPLIER = 3.0
CANDLE_LIMIT = 10

# ==========================================
# PHẦN 2: TRẠM PHÁT SÓNG CHỐNG NGỦ GẬT
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Săn Volume đang chạy 24/7 ngon lành!"

def keep_alive():
    app.run(host='0.0.0.0', port=8080)

# ==========================================
# PHẦN 3: MÃ HOẠT ĐỘNG CỦA BOT
# ==========================================
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload)
    except Exception:
        pass

def get_all_usdt_pairs():
    url = "https://api.binance.com/api/v3/exchangeInfo"
    try:
        res = requests.get(url).json()
        return [s['symbol'] for s in res['symbols'] if s['symbol'].endswith('USDT') and s['status'] == 'TRADING']
    except Exception:
        return []

def check_volume_spike(symbol):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=5m&limit={CANDLE_LIMIT}"
    try:
        res = requests.get(url).json()
        if len(res) < CANDLE_LIMIT: return None
        
        past_volumes = [float(candle[5]) for candle in res[:-1]]
        avg_volume = sum(past_volumes) / len(past_volumes)
        current_volume = float(res[-1][5])
        current_price = float(res[-1][4])
        
        if avg_volume > 0 and current_volume >= (avg_volume * VOLUME_MULTIPLIER):
            return {"price": current_price, "current_vol": current_volume, "avg_vol": avg_volume, "ratio": current_volume / avg_volume}
        return None
    except Exception:
        return None

def run_bot():
    print("🤖 Đang khởi động Bot Săn Volume...")
    send_telegram_message("✅ <b>Bot Săn Dòng Tiền</b> đã đưa lên mây thành công! Đang quét 24/7...")
    while True:
        symbols = get_all_usdt_pairs()
        for symbol in symbols:
            result = check_volume_spike(symbol)
            if result:
                msg = (f"🚨 <b>PHÁT HIỆN ĐỘT BIẾN: {symbol}</b>\n"
                       f"💰 Giá: {result['price']} $\n"
                       f"📊 Vol 5p: {result['current_vol']:,.0f}\n"
                       f"🔥 Tăng: <b>{result['ratio']:.1f} lần</b>\n"
                       f"👉 <a href='https://www.binance.com/en/trade/{symbol.replace('USDT', '_USDT')}'>Mở Binance</a>")
                send_telegram_message(msg)
            time.sleep(0.1)
        time.sleep(300)

# ==========================================
# PHẦN 4: KÍCH HOẠT ĐỒNG THỜI
# ==========================================
# Bật trạm phát sóng web
threading.Thread(target=keep_alive).start()
# Bật bot chạy liên tục
run_bot()
