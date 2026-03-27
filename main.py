import ccxt
import pandas as pd
import requests
import time
import logging
import os
from datetime import datetime
from flask import Flask
from threading import Thread

# ==========================================
# CẤU HÌNH BOT (Gộp từ config.py)
# ==========================================
TELEGRAM_TOKEN = "8700047218:AAHINxefZHAm_fGMEd3sPJMilNtYH36oSy0"
TELEGRAM_CHAT_ID = "7366887130"

TIMEFRAME = '4h'
SLEEP_INTERVAL = 3600
COOLDOWN_TIME = 86400

RSI_PERIOD = 14
RSI_MIN = 50
RSI_MAX = 75

EMA_PERIOD = 20
VOL_SMA_PERIOD = 20
VOL_RATIO_MIN = 3
MIN_24H_QUOTE_VOL = 500000
SWING_WINDOW = 2

# ==========================================
# THIẾT LẬP LOGGING & FLASK (Dành cho Render)
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "Bot Trading đang hoạt động ổn định trên Render!"

def run_web_server():
    # Render sẽ tự động cấp phát PORT thông qua biến môi trường
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# ==========================================
# LOGIC BOT TRADING
# ==========================================
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        logging.error(f"Lỗi gửi Telegram: {e}")

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def check_higher_low(df, window=SWING_WINDOW):
    if len(df) < window * 2 + 1: return False
    lows = []
    for i in range(window, len(df) - window):
        is_swing_low = True
        current_low = df['low'].iloc[i]
        for j in range(1, window + 1):
            if current_low >= df['low'].iloc[i-j] or current_low >= df['low'].iloc[i+j]:
                is_swing_low = False
                break
        if is_swing_low: lows.append(current_low)
    if len(lows) >= 2: return lows[-1] > lows[-2]
    return False

def analyze_pair(exchange, symbol, alerted_pairs):
    try:
        ticker = exchange.fetch_ticker(symbol)
        if ticker.get('quoteVolume', 0) < MIN_24H_QUOTE_VOL: return None
            
        ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        if len(df) < 50: return None
            
        df['ema20'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
        df['rsi14'] = calculate_rsi(df['close'], period=RSI_PERIOD)
        df['vol_sma20'] = df['volume'].rolling(window=VOL_SMA_PERIOD).mean()
        
        idx = -2 
        current_close = df['close'].iloc[idx]
        current_ema20 = df['ema20'].iloc[idx]
        current_rsi = df['rsi14'].iloc[idx]
        current_vol = df['volume'].iloc[idx]
        current_vol_sma = df['vol_sma20'].iloc[idx]
        
        if pd.isna(current_vol_sma) or current_vol_sma == 0: return None
        vol_ratio = current_vol / current_vol_sma
        
        if (current_close > current_ema20) and (RSI_MIN <= current_rsi <= RSI_MAX) and (vol_ratio > VOL_RATIO_MIN) and check_higher_low(df):
            clean_symbol = symbol.split(':')[0].replace('/', '')
            tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{clean_symbol}.P"
            return (f"🚨 <b>TÍN HIỆU FUTURES: {clean_symbol}</b>\n\n"
                    f"💰 <b>Giá:</b> {current_close}\n"
                    f"📊 <b>RSI (4h):</b> {current_rsi:.2f}\n"
                    f"📈 <b>Volume Ratio:</b> {vol_ratio:.2f}x (SMA20)\n\n"
                    f"🔗 <a href='{tv_link}'>Mở biểu đồ TradingView</a>")
    except Exception as e:
        logging.warning(f"Lỗi phân tích {symbol}: {e}")
    return None

def bot_loop():
    logging.info("Khởi động luồng Bot Trading...")
    exchange = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    alerted_pairs = {}
    
    while True:
        try:
            exchange.load_markets()
            symbols = [sym for sym in exchange.symbols if exchange.markets[sym]['linear'] and exchange.markets[sym]['quote'] == 'USDT']
            current_time = time.time()
            
            for symbol in symbols:
                if symbol in alerted_pairs and (current_time - alerted_pairs[symbol]) < COOLDOWN_TIME:
                    continue
                alert_msg = analyze_pair(exchange, symbol, alerted_pairs)
                if alert_msg:
                    send_telegram_message(alert_msg)
                    alerted_pairs[symbol] = current_time
                time.sleep(0.1)
                
            logging.info(f"Hoàn thành quét. Đợi {SLEEP_INTERVAL} giây...")
            time.sleep(SLEEP_INTERVAL)
        except Exception as e:
            logging.error(f"Lỗi vòng lặp bot: {e}. Thử lại sau 60s...")
            time.sleep(60)

if __name__ == '__main__':
    # Chạy Web Server trên một luồng (thread) riêng để không chặn vòng lặp của bot
    web_thread = Thread(target=run_web_server)
    web_thread.start()
    
    # Chạy logic bot ở luồng chính
    bot_loop()
