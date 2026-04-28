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
    return "Bot WickZone đang hoạt động tốt 24/7!"

def run_server():
    port = int(os.environ.get('PORT', 10000))
    # Chạy Flask server tắt cảnh báo log để giao diện console sạch sẽ
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# ==========================================
# CẤU HÌNH CHIẾN LƯỢC BOT
# ==========================================
ZONE_LOOKBACK = 200     # Số nến H4 quét ngược
MIN_WICK_SCORE = 3      # Điểm râu nến tối thiểu (3 = mạnh)
PINBAR_RATIO = 2.5      # Tỷ lệ râu/thân nến Pinbar
REJECT_RATIO = 0.55     # Tỷ lệ râu/tổng chiều dài nến Từ chối
TOUCH_BARS = 5          # Số nến M15 gần nhất cho phép chạm vùng

# Bộ nhớ đệm để chặn spam báo 2 lần cho cùng 1 tín hiệu
alerted_signals = set()

# ==========================================
# 1. HÀM KẾT NỐI API & LẤY DỮ LIỆU
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
    except Exception as e:
        print(f"Lỗi lấy danh sách Binance: {e}")
        return []

def get_top_10_forex_pairs():
    # Chuẩn Ticker của Yahoo Finance
    return ["EURUSD=X", "USDJPY=X", "GBPUSD=X", "USDCHF=X", "AUDUSD=X", 
            "USDCAD=X", "NZDUSD=X", "EURGBP=X", "EURJPY=X", "GBPJPY=X"]

def get_binance_klines(symbol, interval, limit=250):
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
        res = requests.get(url).json()
        df = pd.DataFrame(res, columns=['time', 'open', 'high', 'low', 'close', 'vol', 'close_time', 'qav', 'nat', 'tbb', 'tbq', 'ignore'])
        df = df[['time', 'open', 'high', 'low', 'close']].astype(float)
        return df
    except Exception as e:
        print(f"Lỗi tải nến Binance {symbol}: {e}")
        return pd.DataFrame()

def get_yfinance_klines(symbol, timeframe, limit=250):
    try:
        ticker = yf.Ticker(symbol)
        
        if timeframe == "M15":
            df = ticker.history(interval="15m", period="60d")
        else: # timeframe == "H4"
            df = ticker.history(interval="1h", period="730d") 
            df = df.resample('4h').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last'
            }).dropna()
            
        if df.empty: return pd.DataFrame()
        
        df = df.tail(limit).reset_index()
        time_col = 'Datetime' if 'Datetime' in df.columns else 'Date'
        df.rename(columns={time_col: 'time', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close'}, inplace=True)
        
        df = df[['time', 'open', 'high', 'low', 'close']]
        df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].astype(float)
        df['time'] = df['time'].apply(lambda x: x.timestamp() * 1000)
        
        return df
    except Exception as e:
        print(f"Lỗi tải nến YFinance {symbol}: {e}")
        return pd.DataFrame()

# ==========================================
# 2. LOGIC TÌM VÙNG & TÍN HIỆU
# ==========================================
def calculate_wick_score(row, zone_type):
    high, low, o, c = row['high'], row['low'], row['open'], row['close']
    if high <= low: return 0
    
    body_high, body_low = max(o, c), min(o, c)
    body_size = body_high - body_low
    upper_wick = high - body_high
    lower_wick = body_low - low
    total_size = high - low
    score = 0
    
    if zone_type == 'Supply':
        if upper_wick / total_size > 0.5: score += 3
        elif body_size > 0 and upper_wick > body_size * 3: score += 2
        elif body_size > 0 and upper_wick > body_size * 2: score += 1
    else: # Demand
        if lower_wick / total_size > 0.5: score += 3
        elif body_size > 0 and lower_wick > body_size * 3: score += 2
        elif body_size > 0 and lower_wick > body_size * 2: score += 1
    return score

def scan_h4_zones(df_h4):
    zones = []
    if len(df_h4) < 5: return zones
    
    last_close = df_h4.iloc[-1]['close']
    
    for i in range(2, len(df_h4)-2):
        row = df_h4.iloc[i]
        
        is_fractal_high = (row['high'] >= df_h4.iloc[i-1]['high'] and row['high'] >= df_h4.iloc[i-2]['high'] and
                           row['high'] >= df_h4.iloc[i+1]['high'] and row['high'] >= df_h4.iloc[i+2]['high'])
                           
        is_fractal_low = (row['low'] <= df_h4.iloc[i-1]['low'] and row['low'] <= df_h4.iloc[i-2]['low'] and
                          row['low'] <= df_h4.iloc[i+1]['low'] and row['low'] <= df_h4.iloc[i+2]['low'])
        
        if is_fractal_high and not is_fractal_low:
            score = calculate_wick_score(row, 'Supply')
            if score >= MIN_WICK_SCORE:
                zones.append({
                    'time': row['time'], 'type': 'Supply', 
                    'high': row['high'], 'low': row['close'], 
                    'distal': row['high'], 'proximal': row['close'], 'score': int(score)
                })
                
        elif is_fractal_low and not is_fractal_high:
            score = calculate_wick_score(row, 'Demand')
            if score >= MIN_WICK_SCORE:
                zones.append({
                    'time': row['time'], 'type': 'Demand', 
                    'high': row['close'], 'low': row['low'], 
                    'distal': row['low'], 'proximal': row['close'], 'score': int(score)
                })
                
    valid_zones = []
    for z in zones:
        if z['type'] == 'Supply' and last_close > z['distal']: continue
        if z['type'] == 'Demand' and last_close < z['distal']: continue
        valid_zones.append(z)
        
    return valid_zones

def check_m15_reversal(df_m15, zone_type):
    c1 = df_m15.iloc[-1] 
    c2 = df_m15.iloc[-2] 
    
    bodyH1, bodyL1 = max(c1['open'], c1['close']), min(c1['open'], c1['close'])
    body_size1 = bodyH1 - bodyL1
    upper_wick1 = c1['high'] - bodyH1
    lower_wick1 = bodyL1 - c1['low']
    total_size1 = c1['high'] - c1['low']
    
    if total_size1 <= 0: return False
    
    if zone_type == 'Demand':
        if body_size1 > 0 and lower_wick1 > body_size1 * PINBAR_RATIO: return True
        if lower_wick1 / total_size1 > REJECT_RATIO: return True
        if (c2['close'] < c2['open'] and c1['close'] > c1['open'] and 
            c1['close'] > c2['open'] and c1['open'] <= c2['close']): return True
            
    elif zone_type == 'Supply':
        if body_size1 > 0 and upper_wick1 > body_size1 * PINBAR_RATIO: return True
        if upper_wick1 / total_size1 > REJECT_RATIO: return True
        if (c2['close'] > c2['open'] and c1['close'] < c1['open'] and 
            c1['close'] < c2['open'] and c1['open'] >= c2['close']): return True
            
    return False

# ==========================================
# 3. QUY TRÌNH CHẠY BOT CỐT LÕI
# ==========================================
def process_symbol(symbol, market_type):
    try:
        if market_type == "CRYPTO":
            df_h4 = get_binance_klines(symbol, "4h", 250)
            df_m15 = get_binance_klines(symbol, "15m", 15)
        else:
            df_h4 = get_yfinance_klines(symbol, "H4", 250)
            df_m15 = get_yfinance_klines(symbol, "M15", 15)
            
        if df_h4.empty or df_m15.empty: return

        zones = scan_h4_zones(df_h4)
        if not zones: return
        
        last_m15 = df_m15.iloc[-1]
        close1 = last_m15['close']
        time1 = last_m15['time'] 
        
        recent_m15 = df_m15.tail(TOUCH_BARS)
        
        for z in zones:
            signal_id = f"{symbol}_{z['time']}_{time1}"
            if signal_id in alerted_signals: continue
            
            price_in_zone = (close1 >= z['low'] and close1 <= z['high'])
            touched_recently = any((row['high'] >= z['low'] and row['low'] <= z['high']) for _, row in recent_m15.iterrows())
            
            if price_in_zone or touched_recently:
                if check_m15_reversal(df_m15, z['type']):
                    action = "🟢 MUA (LONG)" if z['type'] == 'Demand' else "🔴 BÁN (SHORT)"
                    clean_symbol = symbol.replace("=X", "") 
                    
                    msg = (
                        f"🚨 <b>TÍN HIỆU {market_type}</b>\n\n"
                        f"Cặp giao dịch: <b>{clean_symbol}</b>\n"
                        f"Hành động: {action}\n"
                        f"Mô hình: Vùng {z['type']} H4 + M15 Đảo chiều\n"
                        f"Điểm Râu H4: {z['score']}/6\n"
                        f"Giá hiện tại: {close1}\n\n"
                        f"<i>*Hãy mở biểu đồ để xác nhận lại cấu trúc!</i>"
                    )
                    send_telegram(msg)
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Đã báo {clean_symbol}")
                    
                    alerted_signals.add(signal_id)
                    if len(alerted_signals) > 1000: alerted_signals.pop()

    except Exception as e:
        pass 

def job_scanner():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Bắt đầu quét thị trường...")
    
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
    # 1. Kích hoạt Server Web chạy song song ở luồng phụ (Tránh lỗi Render)
    server_thread = Thread(target=run_server)
    server_thread.start()

    # 2. Bắt đầu luồng chạy Bot chính
    print("🚀 WickZone Signal Bot Python - Khởi Động!")
    send_telegram("🚀 Bot WickZone Python đã khởi động thành công và bắt đầu quét thị trường!")
    
    job_scanner()
    
    schedule.every().hour.at(":01").do(job_scanner)
    schedule.every().hour.at(":16").do(job_scanner)
    schedule.every().hour.at(":31").do(job_scanner)
    schedule.every().hour.at(":46").do(job_scanner)

    while True:
        schedule.run_pending()
        time.sleep(1)
