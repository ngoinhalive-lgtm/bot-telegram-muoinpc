import os
import time
import requests
import pandas as pd
import schedule
from datetime import datetime

# ==========================================
# CẤU HÌNH THÔNG SỐ (Lấy từ Môi trường Render)
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
OANDA_API_KEY = os.environ.get("OANDA_API_KEY", "YOUR_OANDA_TOKEN")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "YOUR_OANDA_ACC_ID")
OANDA_ENV = "https://api-fxtrade.oanda.com/v3" # Đổi thành api-fxpractice nếu dùng demo

# Cấu hình Bot (Giống EA MQL5)
ZONE_LOOKBACK = 200
MIN_WICK_SCORE = 3
PINBAR_RATIO = 2.5
REJECT_RATIO = 0.55
TOUCH_BARS = 5

# Bộ nhớ đệm để tránh báo lặp tín hiệu
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
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    res = requests.get(url).json()
    # Lọc cặp USDT và sắp xếp theo quoteVolume giảm dần
    usdt_pairs = [x for x in res if x['symbol'].endswith('USDT')]
    sorted_pairs = sorted(usdt_pairs, key=lambda x: float(x['quoteVolume']), reverse=True)
    return [x['symbol'] for x in sorted_pairs[:50]]

def get_top_10_forex_pairs():
    # Top 10 cặp Forex thanh khoản cao nhất
    return ["EUR_USD", "USD_JPY", "GBP_USD", "USD_CHF", "AUD_USD", 
            "USD_CAD", "NZD_USD", "EUR_GBP", "EUR_JPY", "GBP_JPY"]

def get_binance_klines(symbol, interval, limit=250):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    res = requests.get(url).json()
    df = pd.DataFrame(res, columns=['time', 'open', 'high', 'low', 'close', 'vol', 'close_time', 'qav', 'nat', 'tbb', 'tbq', 'ignore'])
    df = df[['time', 'open', 'high', 'low', 'close']].astype(float)
    return df

def get_oanda_klines(symbol, granularity, limit=250):
    headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}
    url = f"{OANDA_ENV}/instruments/{symbol}/candles?count={limit}&price=M&granularity={granularity}"
    res = requests.get(url, headers=headers)
    if res.status_code != 200: return pd.DataFrame()
    
    candles = res.json().get('candles', [])
    data = []
    for c in candles:
        if c['complete']:
            # Chuyển đổi timestamp ISO8601 sang ms
            dt = datetime.strptime(c['time'][:19], "%Y-%m-%dT%H:%M:%S")
            data.append([dt.timestamp() * 1000, float(c['mid']['o']), float(c['mid']['h']), float(c['mid']['l']), float(c['mid']['c'])])
    df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close'])
    return df

# ==========================================
# 2. LOGIC TÌM VÙNG & TÍN HIỆU (Từ MQL5 chuyển sang)
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
    
    # Kiểm tra nến H4 cuối cùng (nến sát hiện tại nhất)
    last_close = df_h4.iloc[-1]['close']
    
    # Tìm Fractal (Bỏ qua 2 nến đầu và 2 nến cuối đang chạy)
    for i in range(2, len(df_h4)-2):
        row = df_h4.iloc[i]
        
        # Fractal High -> Supply
        if (row['high'] >= df_h4.iloc[i-1]['high'] and row['high'] >= df_h4.iloc[i-2]['high'] and
            row['high'] >= df_h4.iloc[i+1]['high'] and row['high'] >= df_h4.iloc[i+2]['high']):
            
            score = calculate_wick_score(row, 'Supply')
            if score >= MIN_WICK_SCORE:
                zone = {
                    'time': row['time'], 'type': 'Supply', 
                    'high': row['high'], 'low': row['close'], 
                    'distal': row['high'], 'proximal': row['close'], 'score': int(score)
                }
                zones.append(zone)
                
        # Fractal Low -> Demand
        elif (row['low'] <= df_h4.iloc[i-1]['low'] and row['low'] <= df_h4.iloc[i-2]['low'] and
              row['low'] <= df_h4.iloc[i+1]['low'] and row['low'] <= df_h4.iloc[i+2]['low']):
            
            score = calculate_wick_score(row, 'Demand')
            if score >= MIN_WICK_SCORE:
                zone = {
                    'time': row['time'], 'type': 'Demand', 
                    'high': row['close'], 'low': row['low'], 
                    'distal': row['low'], 'proximal': row['close'], 'score': int(score)
                }
                zones.append(zone)
                
    # Lọc bỏ các vùng đã bị phá vỡ (Invalidation Rule)
    valid_zones = []
    for z in zones:
        if z['type'] == 'Supply' and last_close > z['distal']: continue
        if z['type'] == 'Demand' and last_close < z['distal']: continue
        valid_zones.append(z)
        
    return valid_zones

def check_m15_reversal(df_m15, zone_type):
    # Nến [i-1] là nến vừa đóng cửa, [i-2] là nến trước đó
    c1 = df_m15.iloc[-1] # Nến đóng cửa gần nhất
    c2 = df_m15.iloc[-2] # Nến trước đó
    
    bodyH1, bodyL1 = max(c1['open'], c1['close']), min(c1['open'], c1['close'])
    body_size1 = bodyH1 - bodyL1
    upper_wick1 = c1['high'] - bodyH1
    lower_wick1 = bodyL1 - c1['low']
    total_size1 = c1['high'] - c1['low']
    
    if total_size1 <= 0: return False
    
    if zone_type == 'Demand':
        # Pinbar tăng
        if body_size1 > 0 and lower_wick1 > body_size1 * PINBAR_RATIO: return True
        # Rejection
        if lower_wick1 / total_size1 > REJECT_RATIO: return True
        # Bullish Engulfing
        if (c2['close'] < c2['open'] and c1['close'] > c1['open'] and 
            c1['close'] > c2['open'] and c1['open'] <= c2['close']): return True
            
    elif zone_type == 'Supply':
        # Pinbar giảm
        if body_size1 > 0 and upper_wick1 > body_size1 * PINBAR_RATIO: return True
        # Rejection
        if upper_wick1 / total_size1 > REJECT_RATIO: return True
        # Bearish Engulfing
        if (c2['close'] > c2['open'] and c1['close'] < c1['open'] and 
            c1['close'] < c2['open'] and c1['open'] >= c2['close']): return True
            
    return False

# ==========================================
# 3. LUỒNG XỬ LÝ CHÍNH
# ==========================================
def process_symbol(symbol, market_type):
    try:
        # 1. Fetch Dữ liệu
        if market_type == "CRYPTO":
            df_h4 = get_binance_klines(symbol, "4h", 250)
            df_m15 = get_binance_klines(symbol, "15m", 15)
        else:
            df_h4 = get_oanda_klines(symbol, "H4", 250)
            df_m15 = get_oanda_klines(symbol, "M15", 15)
            
        if df_h4.empty or df_m15.empty: return

        # 2. Quét vùng H4
        zones = scan_h4_zones(df_h4)
        if not zones: return
        
        # 3. Lấy giá nến M15 vừa đóng cửa
        last_m15 = df_m15.iloc[-1]
        close1 = last_m15['close']
        time1 = last_m15['time'] # Dùng để chặn spam tín hiệu cùng 1 nến
        
        # Lấy 5 nến M15 gần nhất để check chạm vùng
        recent_m15 = df_m15.tail(TOUCH_BARS)
        
        for z in zones:
            # Tạo ID tín hiệu: Tên coin + thời gian tạo vùng + thời gian nến M15 báo tín hiệu
            signal_id = f"{symbol}_{z['time']}_{time1}"
            if signal_id in alerted_signals: continue
            
            # Điều kiện 1: Giá đóng hoặc đã từng chạm vùng trong N nến gần nhất
            price_in_zone = (close1 >= z['low'] and close1 <= z['high'])
            touched_recently = any((row['high'] >= z['low'] and row['low'] <= z['high']) for _, row in recent_m15.iterrows())
            
            if price_in_zone or touched_recently:
                # Điều kiện 2: Có tín hiệu đảo chiều
                if check_m15_reversal(df_m15, z['type']):
                    action = "🟢 MUA (LONG)" if z['type'] == 'Demand' else "🔴 BÁN (SHORT)"
                    msg = (
                        f"🚨 <b>TÍN HIỆU {market_type}</b>\n\n"
                        f"Cặp giao dịch: <b>{symbol}</b>\n"
                        f"Hành động: {action}\n"
                        f"Mô hình: Vùng {z['type']} H4 + M15 Đảo chiều\n"
                        f"Điểm Râu H4: {z['score']}/6\n"
                        f"Giá hiện tại: {close1}\n\n"
                        f"<i>*Hãy mở biểu đồ để xác nhận lại!</i>"
                    )
                    send_telegram(msg)
                    print(f"[{datetime.now()}] Đã báo {symbol}")
                    
                    # Lưu lại để không báo 2 lần cho 1 nến
                    alerted_signals.add(signal_id)
                    # Giữ bộ nhớ đệm sạch (chỉ giữ 1000 tín hiệu gần nhất)
                    if len(alerted_signals) > 1000: alerted_signals.pop()

    except Exception as e:
        print(f"Lỗi xử lý {symbol}: {e}")

def job_scanner():
    print(f"[{datetime.now()}] Đang quét thị trường...")
    # 1. Quét Crypto (Top 50)
    crypto_symbols = get_top_50_binance_futures()
    for sym in crypto_symbols:
        process_symbol(sym, "CRYPTO")
        time.sleep(0.2) # Nghỉ xíu tránh Limit API Binance
        
    # 2. Quét Forex (Top 10)
    forex_symbols = get_top_10_forex_pairs()
    for sym in forex_symbols:
        process_symbol(sym, "FOREX")
        time.sleep(0.5) # Nghỉ xíu tránh Limit API Oanda
        
    print(f"[{datetime.now()}] Hoàn thành chu kỳ quét.")

# Chạy Bot
if __name__ == "__main__":
    print("🚀 WickZone Signal Bot Python - Khởi Động!")
    # Gửi tin nhắn test
    send_telegram("🚀 Bot WickZone Python đã khởi động thành công trên Server!")
    
    # Chạy lần đầu tiên ngay lập tức
    job_scanner()
    
    # Lên lịch chạy cứ mỗi 15 phút (vào các phút 00, 15, 30, 45 của đồng hồ)
    # Vì nến M15 đóng vào các thời điểm này. Chạy vào phút 01 để đảm bảo nến đã đóng hẳn.
    schedule.every().hour.at(":01").do(job_scanner)
    schedule.every().hour.at(":16").do(job_scanner)
    schedule.every().hour.at(":31").do(job_scanner)
    schedule.every().hour.at(":46").do(job_scanner)

    while True:
        schedule.run_pending()
        time.sleep(1)
