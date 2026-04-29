import os
import time
import requests
import pandas as pd
import schedule
from datetime import datetime
import yfinance as yf
from flask import Flask
from threading import Thread

# ==========================================
# CẤU HÌNH THÔNG SỐ BẢO MẬT (Từ Render)
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8700047218:AAHINxefZHAm_fGMEd3sPJMilNtYH36oSy0")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "7366887130")

# ==========================================
# CẤU HÌNH MÁY CHỦ WEB MINI (Giữ Render sống)
# ==========================================
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "Bot Pullback 40% Khung 4H Đang Hoạt Động!"

def run_server():
    port = int(os.environ.get('PORT', 10000))
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# Bộ nhớ đệm chặn spam tín hiệu trùng lặp
alerted_signals = set()

# ==========================================
# 1. HÀM LẤY DỮ LIỆU THỊ TRƯỜNG
# ==========================================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")

def get_top_50_binance_futures():
    try:
        url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        res = requests.get(url).json()
        usdt_pairs = [x for x in res if x['symbol'].endswith('USDT')]
        sorted_pairs = sorted(usdt_pairs, key=lambda x: float(x['quoteVolume']), reverse=True)
        return [x['symbol'] for x in sorted_pairs[:50]]
    except: return []

def get_forex_and_gold():
    # Thêm XAUUSD=X (Vàng) vào danh sách
    return ["EURUSD=X", "USDJPY=X", "GBPUSD=X", "USDCHF=X", "AUDUSD=X", 
            "USDCAD=X", "NZDUSD=X", "EURGBP=X", "EURJPY=X", "GBPJPY=X", "XAUUSD=X"]

def get_binance_klines(symbol, limit=20):
    try:
        # Lấy trực tiếp nến 4H từ Binance
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=4h&limit={limit}"
        res = requests.get(url).json()
        df = pd.DataFrame(res, columns=['time', 'open', 'high', 'low', 'close', 'vol', 'close_time', 'qav', 'nat', 'tbb', 'tbq', 'ignore'])
        df = df[['time', 'open', 'high', 'low', 'close']].astype(float)
        return df
    except: return pd.DataFrame()

def get_yfinance_klines(symbol, limit=100):
    try:
        ticker = yf.Ticker(symbol)
        # Lấy nến 1H và gộp thành 4H (Resampling)
        df = ticker.history(interval="1h", period="30d") 
        if df.empty: return pd.DataFrame()
        
        df = df.resample('4h').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last'
        }).dropna()
        
        df = df.tail(limit).reset_index()
        time_col = 'Datetime' if 'Datetime' in df.columns else 'Date'
        df.rename(columns={time_col: 'time', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close'}, inplace=True)
        df = df[['time', 'open', 'high', 'low', 'close']].astype(float)
        df['time'] = df['time'].apply(lambda x: x.timestamp() * 1000)
        return df
    except: return pd.DataFrame()

# ==========================================
# 2. KIỂM TRA MÔ HÌNH NẾN (CORE)
# ==========================================
def check_candlestick_signal(c1, c2, c3):
    # c1 = Nến 4H đã đóng cửa hoàn toàn
    # c2 = Nến trước đó
    # c3 = Nến trước đó nữa (cho mô hình 3 nến)
    
    body1 = abs(c1['close'] - c1['open'])
    lower_wick1 = min(c1['open'], c1['close']) - c1['low']
    upper_wick1 = c1['high'] - max(c1['open'], c1['close'])
    total_size1 = c1['high'] - c1['low']

    if total_size1 <= 0: return None, None

    # 1. Pinbar / Hammer (Mua)
    if lower_wick1 >= 2 * body1 and upper_wick1 <= body1 and body1 > 0:
        return "MUA", "Bullish Pinbar / Hammer"

    # 2. Pinbar / Shooting Star (Bán)
    if upper_wick1 >= 2 * body1 and lower_wick1 <= body1 and body1 > 0:
        return "BÁN", "Bearish Pinbar / Shooting Star"

    # 3. Bullish Engulfing (Mua)
    if c2['close'] < c2['open'] and c1['close'] > c1['open'] and c1['close'] > c2['open'] and c1['open'] <= c2['close']:
        return "MUA", "Bullish Engulfing"

    # 4. Bearish Engulfing (Bán)
    if c2['close'] > c2['open'] and c1['close'] < c1['open'] and c1['close'] < c2['open'] and c1['open'] >= c2['close']:
        return "BÁN", "Bearish Engulfing"

    # 5. Morning Star (Mua)
    c2_body = abs(c2['close'] - c2['open'])
    c3_body = abs(c3['close'] - c3['open'])
    if c3['close'] < c3['open'] and c2_body < c3_body * 0.3 and c1['close'] > c1['open'] and c1['close'] > (c3['open'] + c3['close']) / 2:
        return "MUA", "Morning Star"

    # 6. Evening Star (Bán)
    if c3['close'] > c3['open'] and c2_body < c3_body * 0.3 and c1['close'] < c1['open'] and c1['close'] < (c3['open'] + c3['close']) / 2:
        return "BÁN", "Evening Star"

    # 7. Tweezer Bottom (Mua) - Sai số râu dưới 0.1%
    if abs(c1['low'] - c2['low']) / (c1['low'] + 0.0001) < 0.001 and c1['close'] > c1['open'] and c2['close'] < c2['open']:
        return "MUA", "Tweezer Bottom"

    # 8. Tweezer Top (Bán) - Sai số râu trên 0.1%
    if abs(c1['high'] - c2['high']) / (c1['high'] + 0.0001) < 0.001 and c1['close'] < c1['open'] and c2['close'] > c2['open']:
        return "BÁN", "Tweezer Top"

    return None, None

# ==========================================
# 3. QUY TRÌNH CHẠY BOT CỐT LÕI
# ==========================================
def process_symbol(symbol, market_type):
    try:
        if market_type == "CRYPTO": df = get_binance_klines(symbol, 20)
        else: df = get_yfinance_klines(symbol, 40)
            
        if df.empty or len(df) < 5: return

        # c_live: Cây nến 4H đang chạy (chưa đóng cửa)
        # c_closed: Cây nến 4H đã đóng cửa hoàn toàn vừa xong
        c_live = df.iloc[-1]
        c_closed = df.iloc[-2]
        c_prev = df.iloc[-3]
        c_prev2 = df.iloc[-4]
        
        # Kiểm tra mô hình trên nến H4 đã ĐÓNG CỬA
        action, pattern = check_candlestick_signal(c_closed, c_prev, c_prev2)
        
        if action and pattern:
            # Tính tổng độ dài của nến H4 tín hiệu
            total_length = c_closed['high'] - c_closed['low']
            if total_length <= 0: return
            
            signal_time = c_closed['time']
            signal_id = f"{symbol}_{signal_time}_{pattern}"
            
            # Bỏ qua nếu đã báo tin nhắn rồi
            if signal_id in alerted_signals: return
            
            is_triggered = False
            entry_price = 0
            
            # --- KIỂM TRA ĐIỀU KIỆN GIÁ HỒI VỀ 40% ---
            if action == "MUA":
                # Canh Mua: Giá phải hồi XUỐNG 40% tính từ đỉnh nến
                entry_price = c_closed['high'] - (0.40 * total_length)
                # Kiểm tra nến H4 hiện tại (c_live) đã quét râu/giá xuống mốc này chưa
                if c_live['low'] <= entry_price:
                    is_triggered = True
                    
            elif action == "BÁN":
                # Canh Bán: Giá phải hồi LÊN 40% tính từ đáy nến
                entry_price = c_closed['low'] + (0.40 * total_length)
                # Kiểm tra nến H4 hiện tại (c_live) đã quét râu/giá lên mốc này chưa
                if c_live['high'] >= entry_price:
                    is_triggered = True
                    
            # Nếu giá đã quét đúng mốc 40% -> Báo Telegram!
            if is_triggered:
                clean_symbol = symbol.replace("=X", "")
                icon = "🟢" if action == "MUA" else "🔴"
                msg = (
                    f"🚨 <b>TÍN HIỆU SẾP ƠI {market_type}</b>\n\n"
                    f"Cặp giao dịch: <b>{clean_symbol}</b>\n"
                    f"Chiến lược: Canh hồi nến tín hiệu\n"
                    f"Hành động: {icon} <b>{action}</b>\n"
                    f"Mô hình: {pattern}\n"
                    f"Giá đóng H4: {c_closed['close']:.5f}\n"
                    f"Vùng khớp lệnh: <b>{entry_price:.5f}</b>\n\n"
                    f"✅ <b>Trạng thái:</b> Giá đã chạm vùng ngon cơm!\n"
                    f"<i>*Hãy mở biểu đồ để xem chi tiết cấu trúc!</i>"
                )
                send_telegram(msg)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Đã báo {clean_symbol} - {pattern}")
                
                # Lưu vào bộ nhớ để không báo spam lại
                alerted_signals.add(signal_id)
                if len(alerted_signals) > 1000: alerted_signals.pop()

    except Exception as e: pass

def job_scanner():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Đang quét theo dõi Pullback 4H...")
    
    crypto_symbols = get_top_50_binance_futures()
    for sym in crypto_symbols:
        process_symbol(sym, "CRYPTO")
        time.sleep(0.2) 
        
    forex_symbols = get_forex_and_gold()
    for sym in forex_symbols:
        process_symbol(sym, "FOREX")
        time.sleep(0.5)
        
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Quét hoàn tất, chờ chu kỳ tiếp theo.")

# ==========================================
# KHỞI ĐỘNG CHƯƠNG TRÌNH
# ==========================================
if __name__ == "__main__":
    # 1. Bật máy chủ Web giữ Render sống
    server_thread = Thread(target=run_server)
    server_thread.start()

    # 2. Khởi động Bot
    print("🚀 Em Yêu Anh - Khởi Động!")
    send_telegram("🚀 Vợ đã khởi động thành công!")
    
    # Chạy lần đầu tiên
    job_scanner()
    
    # Lên lịch quét: Chạy 5 PHÚT MỘT LẦN.
    # Lý do: Ta cần kiểm tra liên tục xem "cây nến 4H đang chạy" đã hồi giá về mốc 40% chưa.
    schedule.every(5).minutes.do(job_scanner)

    while True:
        schedule.run_pending()
        time.sleep(1)
