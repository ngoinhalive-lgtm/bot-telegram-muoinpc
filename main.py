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
MOTHER_BAR_BODY_PCT = 2.5  
VOL_MULTIPLIER = 1.5       # Đã tăng lên 1.5 theo yêu cầu của bạn

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
# Thêm bộ nhớ để bot không báo trùng 1 tín hiệu nhiều lần
alerted_candles = {}

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload)
    except Exception:
        pass

def get_futures_pairs():
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    try:
        res = requests.get(url).json()
        return [s['symbol'] for s in res['symbols'] if s['quoteAsset'] == 'USDT' and s['contractType'] == 'PERPETUAL' and s['status'] == 'TRADING']
    except Exception:
        return []

def get_all_24h_volumes():
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    try:
        res = requests.get(url).json()
        return {item['symbol']: float(item['quoteVolume']) for item in res}
    except Exception:
        return {}

def check_inside_bar_pattern(symbol):
    # Lấy 11 nến để có đủ dữ liệu lùi lại 1 nhịp
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=4h&limit=11"
    try:
        res = requests.get(url).json()
        if len(res) < 5: return None
        
        # --- ĐIỀU KIỆN MỚI: Chỉ lấy nến ĐÃ ĐÓNG CỬA ---
        # res[-1] là nến đang chạy.
        # res[-2] là nến 4H vừa mới đóng cửa hoàn toàn.
        last_closed_idx = -2
        
        # Lùi về quá khứ để tìm Nến Chủ (từ nến -4 đến -11)
        for mb_idx in range(-4, -11, -1):
            mb = res[mb_idx]
            mb_open = float(mb[1])
            mb_close = float(mb[4])
            mb_body_high = max(mb_open, mb_close)
            mb_body_low = min(mb_open, mb_close)
            
            # 1. Kiểm tra xem Nến Chủ có đủ mạnh không
            mb_body_pct = ((mb_body_high - mb_body_low) / mb_body_low) * 100
            if mb_body_pct < MOTHER_BAR_BODY_PCT:
                continue 
                
            is_valid_inside = True
            inside_vols = []
            
            # 2. Kiểm tra các nến con (từ sau Nến Chủ đến nến VỪA ĐÓNG CỬA)
            for i in range(mb_idx + 1, last_closed_idx + 1): 
                ib = res[i]
                ib_open = float(ib[1])
                ib_close = float(ib[4])
                ib_body_high = max(ib_open, ib_close)
                ib_body_low = min(ib_open, ib_close)
                
                # Thân nến con phải nằm trọn trong High/Low của thân Nến Chủ (Râu tự do)
                if ib_body_high > mb_body_high or ib_body_low < mb_body_low:
                    is_valid_inside = False
                    break
                
                # Lưu lại Volume của các nến con CŨ (không tính nến đóng cửa gần nhất)
                if i != last_closed_idx: 
                    inside_vols.append(float(ib[5]))

            # 3. Nếu cấu trúc nến đúng chuẩn
            if is_valid_inside and len(inside_vols) >= 1:
                current_candle = res[last_closed_idx] # Nến vừa đóng cửa
                current_vol = float(current_candle[5])
                current_close = float(current_candle[4])
                current_open_time = current_candle[0] # Thời gian mở nến để làm ID
                
                max_prev_vol = max(inside_vols)
                
                # 4. Kiểm tra Volume trội hơn 1.5 lần
                if max_prev_vol > 0 and current_vol >= (max_prev_vol * VOL_MULTIPLIER):
                    
                    # KIỂM TRA CHỐNG SPAM: Nếu nến này đã báo rồi thì bỏ qua
                    if alerted_candles.get(symbol) == current_open_time:
                        return None 
                        
                    # Nếu chưa báo thì lưu vào "sổ tay" và trả kết quả
                    alerted_candles[symbol] = current_open_time
                    
                    return {
                        "price": current_close,
                        "mb_pct": mb_body_pct,
                        "inside_count": abs(mb_idx) - 2, 
                        "ratio": current_vol / max_prev_vol
                    }
        return None
    except Exception:
        return None

def run_bot():
    print("🤖 Đang khởi động Bot Sniper Futures 4H (Bản Fix Spam)...")
    send_telegram_message("✅ <b>Bot Futures 4H</b> đã kích hoạt! Chỉ bắt tín hiệu khi nến đã đóng hoàn toàn.")
    while True:
        symbols = get_futures_pairs()
        volumes_24h = get_all_24h_volumes() 
        
        for symbol in symbols:
            result = check_inside_bar_pattern(symbol)
            if result:
                vol_24h = volumes_24h.get(symbol, 0)
                
                msg = (f"🎯 <b>CỤM NẾN NÉN FUTURES (4H): {symbol}</b>\n"
                       f"💰 Giá đóng cửa: {result['price']} $\n"
                       f"📈 Nến chủ: Thân dài <b>{result['mb_pct']:.1f}%</b>\n"
                       f"🗜️ Cấu trúc: Có <b>{result['inside_count']} nến con</b> nằm trọn trong thân\n"
                       f"🔥 Volume nến Break: Trội hơn <b>{result['ratio']:.1f} lần</b> nến trước\n"
                       f"💵 Thanh khoản 24h: <b>{vol_24h / 1000000:.1f} Triệu $</b>\n"
                       f"👉 <a href='https://www.binance.com/en/futures/{symbol}'>Mở biểu đồ Futures</a>")
                send_telegram_message(msg)
            
            time.sleep(0.1)
            
        print("Đã quét xong. Đang chờ...")
        # Vì đã chống spam, có thể rút ngắn thời gian quét xuống 1 phút (60s) để báo tin nhanh hơn ngay sau khi nến đóng
        time.sleep(60) 

# ==========================================
# KÍCH HOẠT HỆ THỐNG
# ==========================================
threading.Thread(target=keep_alive).start()
run_bot()
