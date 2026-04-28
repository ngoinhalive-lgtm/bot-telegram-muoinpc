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
# CẤU HÌNH THÔNG SỐ BẢO MẬT (Lấy từ Environment Variables của Render)
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8700047218:AAHINxefZHAm_fGMEd3sPJMilNtYH36oSy0")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "7366887130")

# ==========================================
# CẤU HÌNH MÁY CHỦ WEB MINI (ĐỂ RENDER KHÔNG BÁO LỖI TIMEOUT)
# ==========================================
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "Bot Giao Dịch Thuận Xu Hướng (BB + Nến 1H) Đang Hoạt Động!"

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

def get_top_10_forex_pairs():
    return ["EURUSD=X", "USDJPY=X", "GBPUSD=X", "USDCHF=X", "AUDUSD=X", 
            "USDCAD=X", "NZDUSD=X", "EURGBP=X", "EURJPY=X", "GBPJPY=X"]

def get_binance_klines(symbol, limit=100):
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h&limit={limit}"
        res = requests.get(url).json()
        df = pd.DataFrame(res, columns=['time', 'open', 'high', 'low', 'close', 'vol', 'close_time', 'qav', 'nat', 'tbb', 'tbq', 'ignore'])
        df = df[['time', 'open', 'high', 'low', 'close']].astype(float)
        return df
    except: return pd.DataFrame()

def get_yfinance_klines(symbol, limit=100):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(interval="1h", period="30d") 
        if df.empty: return pd.DataFrame()
        df = df.tail(limit).reset_index()
        time_col = 'Datetime' if 'Datetime' in df.columns else 'Date'
        df.rename(columns={time_col: 'time', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close'}, inplace=True)
        df = df[['time', 'open', 'high', 'low', 'close']].astype(float)
        df['time'] = df['time'].apply(lambda x: x.timestamp() * 1000)
        return df
    except: return pd.DataFrame()

# ==========================================
# 2. THUẬT TOÁN TÌM TÍN HIỆU (NEW)
# ==========================================
def calculate_indicators(df):
    # Tính Bollinger Bands (20, 2)
    df['sma_20'] = df['close'].rolling(window=20).mean()
    df['std_20'] = df['close'].rolling(window=20).std()
    df['upper_band'] = df['sma_20'] + (df['std_20'] * 2)
    df['lower_band'] = df['sma_20'] - (df['std_20'] * 2)
    return df

def get_trend_24h(df):
    # Lấy 24 nến gần nhất (trừ nến cuối cùng chưa đóng cửa nếu có)
    if len(df) < 25: return "SIDEWAYS"
    df_24 = df.iloc[-25:-1] 
    
    # Chia 24 nến làm 2 nửa (12 nến đầu và 12 nến sau) để xác định cấu trúc LL/HH
    first_half = df_24.iloc[:12]
    second_half = df_24.iloc[12:]
    
    h1, l1 = first_half['high'].max(), first_half['low'].min()
    h2, l2 = second_half['high'].max(), second_half['low'].min()
    
    # Higher High + Higher Low = Uptrend
    if h2 > h1 and l2 > l1: return "UPTREND"
    # Lower High + Lower Low = Downtrend
    elif h2 < h1 and l2 < l1: return "DOWNTREND"
    else: return "SIDEWAYS"

def check_candlestick_signal(df, trend):
    # c1 = Nến vừa đóng cửa (Tín hiệu)
    # c2 = Nến ngay trước đó
    # c3 = Nến trước nữa (dành cho mô hình 3 nến)
    c1 = df.iloc[-1]
    c2 = df.iloc[-2]
    c3 = df.iloc[-3]
    
    # --- ĐIỀU KIỆN CHẠM BOLLINGER BANDS ---
    # Chạm băng Dưới/Giữa: Mức giá thấp nhất của nến phải <= đường SMA20 (Tức là nằm dưới băng giữa hoặc băng dưới)
    touch_lower_or_mid = (c1['low'] <= c1['sma_20'])
    # Chạm băng Trên/Giữa: Mức giá cao nhất của nến phải >= đường SMA20 (Tức là nằm trên băng giữa hoặc băng trên)
    touch_upper_or_mid = (c1['high'] >= c1['sma_20'])

    # --- HÀM KIỂM TRA MÔ HÌNH NẾN ---
    def is_engulfing(c1, c2, bullish=True):
        if bullish: return c2['close'] < c2['open'] and c1['close'] > c1['open'] and c1['close'] > c2['open'] and c1['open'] <= c2['close']
        else: return c2['close'] > c2['open'] and c1['close'] < c1['open'] and c1['close'] < c2['open'] and c1['open'] >= c2['close']

    def is_pinbar(c, bullish=True):
        body = abs(c['close'] - c['open'])
        lower_wick = min(c['open'], c['close']) - c['low']
        upper_wick = c['high'] - max(c['open'], c['close'])
        if bullish: return lower_wick >= 2 * body and upper_wick <= body and body > 0 # Hammer
        else: return upper_wick >= 2 * body and lower_wick <= body and body > 0 # Shooting Star
        
    def is_star(c1, c2, c3, morning=True):
        c2_small = abs(c2['close'] - c2['open']) < abs(c3['close'] - c3['open']) * 0.3 # Nến giữa thân nhỏ
        if morning:
            return c3['close'] < c3['open'] and c2_small and c1['close'] > c1['open'] and c1['close'] > (c3['open'] + c3['close']) / 2
        else:
            return c3['close'] > c3['open'] and c2_small and c1['close'] < c1['open'] and c1['close'] < (c3['open'] + c3['close']) / 2

    def is_tweezer(c1, c2, bottom=True):
        if bottom:
            diff = abs(c1['low'] - c2['low']) / c1['low']
            return diff < 0.001 and c1['close'] > c1['open'] and c2['close'] < c2['open']
        else:
            diff = abs(c1['high'] - c2['high']) / c1['high']
            return diff < 0.001 and c1['close'] < c1['open'] and c2['close'] > c2['open']

    # --- KIỂM TRA KẾT HỢP ---
    if trend == "UPTREND" and touch_lower_or_mid:
        if is_engulfing(c1, c2, bullish=True): return "MUA", "Bullish Engulfing"
        if is_pinbar(c1, bullish=True): return "MUA", "Bullish Pinbar / Hammer"
        if is_star(c1, c2, c3, morning=True): return "MUA", "Morning Star"
        if is_tweezer(c1, c2, bottom=True): return "MUA", "Tweezer Bottom"
        
    elif trend == "DOWNTREND" and touch_upper_or_mid:
        if is_engulfing(c1, c2, bullish=False): return "BÁN", "Bearish Engulfing"
        if is_pinbar(c1, bullish=False): return "BÁN", "Bearish Pinbar / Shooting Star"
        if is_star(c1, c2, c3, morning=False): return "BÁN", "Evening Star"
        if is_tweezer(c1, c2, bottom=False): return "BÁN", "Tweezer Top"
        
    return None, None

# ==========================================
# 3. QUY TRÌNH CHẠY BOT CỐT LÕI
# ==========================================
def process_symbol(symbol, market_type):
    try:
        # Lấy tối thiểu 50 nến để tính mượt đường Bollinger 20
        if market_type == "CRYPTO": df = get_binance_klines(symbol, 60)
        else: df = get_yfinance_klines(symbol, 60)
            
        if df.empty or len(df) < 30: return

        df = calculate_indicators(df)
        trend = get_trend_24h(df)
        
        # Nếu Sideways thì bỏ qua không tìm tín hiệu
        if trend == "SIDEWAYS": return
        
        action, pattern = check_candlestick_signal(df, trend)
        
        if action and pattern:
            time_signal = df.iloc[-1]['time']
            signal_id = f"{symbol}_{time_signal}_{pattern}"
            
            if signal_id not in alerted_signals:
                clean_symbol = symbol.replace("=X", "")
                icon = "🟢" if action == "MUA" else "🔴"
                msg = (
                    f"🚨 <b>TÍN HIỆU 1H {market_type}</b>\n\n"
                    f"Cặp giao dịch: <b>{clean_symbol}</b>\n"
                    f"Xu hướng (24H): <b>{trend}</b>\n"
                    f"Hành động: {icon} <b>{action}</b>\n"
                    f"Mô hình: {pattern} chạm Bollinger Bands\n"
                    f"Giá đóng cửa: {df.iloc[-1]['close']}\n\n"
                    f"<i>*Vui lòng check chart để xác nhận lại!</i>"
                )
                send_telegram(msg)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Đã báo {clean_symbol} - {pattern}")
                
                alerted_signals.add(signal_id)
                if len(alerted_signals) > 1000: alerted_signals.pop()

    except Exception as e: pass

def job_scanner():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Bắt đầu quét thị trường 1H...")
    
    crypto_symbols = get_top_50_binance_futures()
    for sym in crypto_symbols:
        process_symbol(sym, "CRYPTO")
        time.sleep(0.2) 
        
    forex_symbols = get_top_10_forex_pairs()
    for sym in forex_symbols:
        process_symbol(sym, "FOREX")
        time.sleep(0.5)
        
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Hoàn thành chu kỳ quét.")

# ==========================================
# KHỞI ĐỘNG CHƯƠNG TRÌNH
# ==========================================
if __name__ == "__main__":
    # 1. Bật máy chủ Web giữ Render sống
    server_thread = Thread(target=run_server)
    server_thread.start()

    # 2. Khởi động thông báo Telegram
    print("🚀 Bot Trend Following (BB+Nến) - Khởi Động!")
    send_telegram("🚀 Bot Giao Dịch Thuận Xu Hướng đã khởi động thành công trên Server!")
    
    # Chạy lần đầu tiên
    job_scanner()
    
    # Lên lịch quét: CHỈ chạy 1 lần vào PHÚT THỨ 01 của mỗi giờ (VD: 01:01, 02:01, 03:01)
    # Vì chúng ta đang dùng nến 1H, chạy quét 1 lần/tiếng lúc nến vừa đóng xong là chuẩn xác nhất.
    schedule.every().hour.at(":01").do(job_scanner)

    while True:
        schedule.run_pending()
        time.sleep(1)
