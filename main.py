import requests
import time
import threading
from flask import Flask

# ==========================================
# PHẦN 1: CÀI ĐẶT THÔNG SỐ CHIẾN THUẬT
# ==========================================
TOKEN = '8700047218:AAHINxefZHAm_fGMEd3sPJMilNtYH36oSy0'
CHAT_ID = '7366887130'

# --- Thông số Dòng tiền ---
VOLUME_MULTIPLIER = 10.0   # Volume nến hiện tại phải cao gấp 3 lần trung bình

# --- Thông số Cú nén (Tích lũy) ---
COMPRESSION_CANDLES = 20  # Lấy 20 cây nến trước đó để xét xem có nén hay không
MAX_SPREAD_PERCENT = 5.0  # Trong 20 nến đó, chênh lệch đỉnh/đáy tối đa chỉ được 5% (Giá đi ngang)

# ==========================================
# PHẦN 2: TRẠM PHÁT SÓNG CHỐNG NGỦ GẬT
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Lò Xo Nén đang chạy 24/7!"

def keep_alive():
    app.run(host='0.0.0.0', port=8080)

# ==========================================
# PHẦN 3: XỬ LÝ DỮ LIỆU & BỘ LỌC ĐA LỚP
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

def check_breakout(symbol):
    # Lấy dữ liệu nến: Bao gồm số nến nén + 1 nến hiện tại
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=5m&limit={COMPRESSION_CANDLES + 1}"
    try:
        res = requests.get(url).json()
        if len(res) < COMPRESSION_CANDLES + 1: return None

        # Tách nến quá khứ (vùng nén) và nến hiện tại (nến phá vỡ)
        past_candles = res[:-1]
        current_candle = res[-1]

        # 1. Kiểm tra Dòng tiền (Volume)
        past_volumes = [float(c[5]) for c in past_candles]
        avg_volume = sum(past_volumes) / len(past_volumes)
        current_volume = float(current_candle[5])

        if avg_volume == 0 or current_volume < (avg_volume * VOLUME_MULTIPLIER):
            return None # Không đủ volume

        # 2. Kiểm tra Cú nén (Compression)
        past_highs = [float(c[2]) for c in past_candles]
        past_lows = [float(c[3]) for c in past_candles]
        max_high = max(past_highs)
        min_low = min(past_lows)

        spread = ((max_high - min_low) / min_low) * 100
        if spread > MAX_SPREAD_PERCENT:
            return None # Biên độ dao động quá lớn, không phải vùng nén

        # 3. Kiểm tra Phá vỡ (Breakout)
        current_close = float(current_candle[4])
        if current_close <= max_high:
            return None # Volume to nhưng giá chưa phá qua đỉnh cũ -> Bỏ qua

        # Nếu vượt qua cả 3 vòng kiểm tra:
        return {
            "price": current_close,
            "current_vol": current_volume,
            "ratio": current_volume / avg_volume,
            "spread": spread
        }
    except Exception:
        return None

def run_bot():
    print("🤖 Đang khởi động Bot Lò Xo...")
    send_telegram_message("✅ <b>Bot Siêu Lọc (Breakout + Volume)</b> đã bật! Đang quét thị trường...")
    while True:
        symbols = get_all_usdt_pairs()
        for symbol in symbols:
            result = check_breakout(symbol)
            if result:
                msg = (f"🚀 <b>PHÁ VỠ CÚ NÉN: {symbol}</b>\n"
                       f"💰 Giá: {result['price']} $\n"
                       f"🗜️ Biên độ nén: {result['spread']:.1f}%\n"
                       f"🔥 Volume bùng nổ: <b>Gấp {result['ratio']:.1f} lần</b>\n"
                       f"👉 <a href='https://www.binance.com/en/trade/{symbol.replace('USDT', '_USDT')}'>Xem biểu đồ ngay</a>")
                send_telegram_message(msg)
            time.sleep(0.1)
        time.sleep(300)

# Kích hoạt bot
threading.Thread(target=keep_alive).start()
run_bot()
