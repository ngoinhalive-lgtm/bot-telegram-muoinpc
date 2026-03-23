import requests
import time
import threading
from flask import Flask

# ==========================================
# PHẦN 1: CÀI ĐẶT THÔNG SỐ CHIẾN THUẬT FUTURES
# ==========================================
TOKEN = '8700047218:AAHINxefZHAm_fGMEd3sPJMilNtYH36oSy0'
CHAT_ID = '7366887130'

# --- Thông số Cụm nến Inside Bar ---
MOTHER_BAR_BODY_PCT = 2.5  # Thân nến chủ phải dài ít nhất 2.5% (Thể hiện nến đà mạnh)
VOL_MULTIPLIER = 1.2       # Nến con hiện tại có Volume cao hơn nến con trước ít nhất 1.2 lần (trội hơn một tý)

# ==========================================
# PHẦN 2: TRẠM PHÁT SÓNG CHỐNG NGỦ GẬT
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Sniper Futures 4H đang chạy 24/7!"

def keep_alive():
    app.run(host='0.0.0.0', port=8080)

# ==========================================
# PHẦN 3: XỬ LÝ DỮ LIỆU SÀN FUTURES
# ==========================================
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload)
    except Exception:
        pass

def get_futures_pairs():
    """Lấy danh sách các cặp USDT Futures đang giao dịch"""
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    try:
        res = requests.get(url).json()
        return [s['symbol'] for s in res['symbols'] if s['quoteAsset'] == 'USDT' and s['contractType'] == 'PERPETUAL' and s['status'] == 'TRADING']
    except Exception:
        return []

def get_all_24h_volumes():
    """Lấy Volume 24h của TẤT CẢ đồng coin chỉ bằng 1 lần gọi (Tối ưu tốc độ)"""
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    try:
        res = requests.get(url).json()
        # Lưu dưới dạng { 'BTCUSDT': 150000000.5, ... } (Tính bằng USD)
        return {item['symbol']: float(item['quoteVolume']) for item in res}
    except Exception:
        return {}

def check_inside_bar_pattern(symbol):
    """Săn tìm cụm nến nén khung 4H"""
    # Lấy 10 cây nến 4H gần nhất
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=4h&limit=10"
    try:
        res = requests.get(url).json()
        if len(res) < 4: return None
        
        # Lùi về quá khứ để tìm Nến Chủ (từ nến -3 đến -9)
        for mb_idx in range(-3, -10, -1):
            mb = res[mb_idx]
            mb_open = float(mb[1])
            mb_close = float(mb[4])
            mb_body_high = max(mb_open, mb_close)
            mb_body_low = min(mb_open, mb_close)
            
            # 1. Kiểm tra xem Nến Chủ có đủ mạnh không
            mb_body_pct = ((mb_body_high - mb_body_low) / mb_body_low) * 100
            if mb_body_pct < MOTHER_BAR_BODY_PCT:
                continue # Nến yếu quá, bỏ qua tìm nến khác
                
            is_valid_inside = True
            inside_vols = []
            
            # 2. Kiểm tra các nến con sau Nến Chủ xem có nằm trọn trong thân không
            for i in range(mb_idx + 1, 0): # Chạy từ nến con đầu tiên đến nến hiện tại (-1)
                ib = res[i]
                ib_open = float(ib[1])
                ib_close = float(ib[4])
                ib_body_high = max(ib_open, ib_close)
                ib_body_low = min(ib_open, ib_close)
                
                # Thân nến con phải nằm trọn trong High/Low của thân Nến Chủ (Râu tự do)
                if ib_body_high > mb_body_high or ib_body_low < mb_body_low:
                    is_valid_inside = False
                    break
                
                # Lưu lại Volume của các nến con CŨ (không tính nến hiện tại đang chạy)
                if i != -1: 
                    inside_vols.append(float(ib[5]))

            # 3. Nếu cấu trúc nến đúng chuẩn và có ít nhất 1 nến con cũ để so sánh
            if is_valid_inside and len(inside_vols) >= 1:
                current_candle = res[-1]
                current_vol = float(current_candle[5])
                current_close = float(current_candle[4])
                
                max_prev_vol = max(inside_vols)
                
                # 4. Kiểm tra Volume nến con hiện tại có "trội" hơn các nến con trước không
                if max_prev_vol > 0 and current_vol >= (max_prev_vol * VOL_MULTIPLIER):
                    return {
                        "price": current_close,
                        "mb_pct": mb_body_pct,
                        "inside_count": abs(mb_idx) - 1, # Tổng số nến con
                        "ratio": current_vol / max_prev_vol
                    }
        return None
    except Exception:
        return None

def run_bot():
    print("🤖 Đang khởi động Bot Sniper Futures 4H...")
    send_telegram_message("✅ <b>Bot Futures 4H</b> đã kích hoạt! Sẵn sàng săn nến Inside Bar...")
    while True:
        symbols = get_futures_pairs()
        volumes_24h = get_all_24h_volumes() # Kéo volume 24h của cả sàn về
        
        for symbol in symbols:
            result = check_inside_bar_pattern(symbol)
            if result:
                vol_24h = volumes_24h.get(symbol, 0)
                
                msg = (f"🎯 <b>CỤM NẾN NÉN FUTURES (4H): {symbol}</b>\n"
                       f"💰 Giá hiện tại: {result['price']} $\n"
                       f"📈 Nến chủ: Thân dài <b>{result['mb_pct']:.1f}%</b>\n"
                       f"🗜️ Cấu trúc: Có <b>{result['inside_count']} nến con</b> nằm trọn trong thân\n"
                       f"🔥 Volume nến cuối: Trội hơn <b>{result['ratio']:.1f} lần</b> nến trước\n"
                       f"💵 Thanh khoản 24h: <b>{vol_24h / 1000000:.1f} Triệu $</b>\n"
                       f"👉 <a href='https://www.binance.com/en/futures/{symbol}'>Mở biểu đồ Futures</a>")
                send_telegram_message(msg)
            
            time.sleep(0.1) # Tránh bị sàn chặn API
            
        print("Đã quét xong 1 vòng Futures. Nghỉ ngơi...")
        time.sleep(300) # Chu kỳ quét 5 phút một lần để check nến 4H

# ==========================================
# KÍCH HOẠT HỆ THỐNG
# ==========================================
threading.Thread(target=keep_alive).start()
run_bot()
