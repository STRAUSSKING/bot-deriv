import json
import time
import requests
import numpy as np
import pandas as pd
import websocket
import threading

# ==========================================
# ⚙️ CONFIGURATION DES PARAMÈTRES
# ==========================================
TELEGRAM_BOT_TOKEN = "8900570872:AAGHVeWBDobqPqJ4D_b74npZ_I89uMY5-_A"
TELEGRAM_CHAT_ID = "6365221307"

DERIV_APP_ID = "1089"  # App ID public par défaut
WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

# Liste des 10 actifs à surveiller
SYMBOLS = {
    "R_10": "Volatility 10",
    "1HZ10V": "Volatility 10 (1s)",
    "R_25": "Volatility 25",
    "1HZ25V": "Volatility 25 (1s)",
    "R_50": "Volatility 50",
    "1HZ50V": "Volatility 50 (1s)",
    "R_75": "Volatility 75",
    "1HZ75V": "Volatility 75 (1s)",
    "R_100": "Volatility 100",
    "1HZ100V": "Volatility 100 (1s)"
}

# Stockage des historiques de bougies (en mémoire)
candles_data = {symbol: [] for symbol in SYMBOLS}

# ==========================================
# 📲 FONCTION D'ENVOI TELEGRAM
# ==========================================
def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Erreur envoi Telegram: {e}")

# ==========================================
# 📊 CALCULS D'INDICATEURS TECHNIQUES
# ==========================================
def calculate_indicators(df):
    # EMA 15 et EMA 40
    df['EMA15'] = df['close'].ewm(span=15, adjust=False).mean()
    df['EMA40'] = df['close'].ewm(span=40, adjust=False).mean()
    
    # ATR 14 (Average True Range)
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    df['ATR'] = true_range.rolling(14).mean()
    
    return df

# ==========================================
# 🎯 STRATÉGIE & DÉTECTION DES SIGNAUX
# ==========================================
def analyze_market(symbol):
    data = candles_data[symbol]
    if len(data) < 50:
        return

    df = pd.DataFrame(data)
    df = calculate_indicators(df)
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    symbol_name = SYMBOLS[symbol]
    
    price = last['close']
    atr = last['ATR'] if not np.isnan(last['ATR']) else (price * 0.001)

    # Condition CONFIG 3 : Croisement EMA 15/40 avec filtre ATR
    ema_bullish = prev['EMA15'] <= prev['EMA40'] and last['EMA15'] > last['EMA40']
    ema_bearish = prev['EMA15'] >= prev['EMA40'] and last['EMA15'] < last['EMA40']

    if ema_bullish:
        sl = round(price - (atr * 1.5), 5)
        tp1 = round(price + (atr * 1.0), 5)
        tp_final = round(price + (atr * 2.0), 5)
        
        msg = (
            f"⚡ <b>[SYNTHÉTIQUE DERIV] PRÉPARATION D'ORDRE (CONFIG 3)</b>\n"
            f"📈 <b>Actif :</b> {symbol_name}\n"
            f"📌 <b>Ordre Suggéré :</b> MARKET / IMMÉDIAT\n"
            f"🎯 <b>Zone d'Entrée :</b> {price}\n"
            f"🔴 <b>Stop Loss :</b> {sl}\n"
            f"🎯 <b>TP1 (50% - Partiel) :</b> {tp1}\n"
            f"✅ <b>Take Profit Final (R:R 1:1.5) :</b> {tp_final}\n"
            f"💡 <b>Action :</b> Achat au marché immédiat"
        )
        send_telegram_msg(msg)

    elif ema_bearish:
        sl = round(price + (atr * 1.5), 5)
        tp1 = round(price - (atr * 1.0), 5)
        tp_final = round(price - (atr * 2.0), 5)
        
        msg = (
            f"🚨 <b>[SYNTHÉTIQUE DERIV] EXÉCUTION IMMÉDIATE (CONFIG 3)</b>\n"
            f"📉 <b>Actif :</b> {symbol_name}\n"
            f"🔄 <b>Direction :</b> SELL\n"
            f"🎯 <b>Prix du Marché :</b> {price}\n"
            f"🔴 <b>Stop Loss Protégé :</b> {sl}\n"
            f"🎯 <b>TP1 (50% - Partiel) :</b> {tp1}\n"
            f"✅ <b>Take Profit Final :</b> {tp_final}"
        )
        send_telegram_msg(msg)

# ==========================================
# 🔌 GESTION DU WEBSOCKET DERIV
# ==========================================
def on_message(ws, message):
    data = json.loads(message)
    msg_type = data.get("msg_type")

    if msg_type == "ohlc":
        ohlc = data.get("ohlc")
        symbol = ohlc.get("symbol")
        if symbol in candles_data:
            candle = {
                "open": float(ohlc["open"]),
                "high": float(ohlc["high"]),
                "low": float(ohlc["low"]),
                "close": float(ohlc["close"]),
                "epoch": int(ohlc["open_time"])
            }
            candles_data[symbol].append(candle)
            if len(candles_data[symbol]) > 100:
                candles_data[symbol].pop(0)
            
            analyze_market(symbol)

    elif msg_type == "candles":
        symbol = data.get("echo_req", {}).get("ticks_history")
        req_candles = data.get("candles", [])
        if symbol in candles_data and req_candles:
            candles_data[symbol] = [
                {
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "epoch": int(c["epoch"])
                } for c in req_candles
            ]
            print(f"✅ Historique chargé pour {SYMBOLS.get(symbol, symbol)}")
            # Abonnement au flux en direct
            sub_req = {"ticks_history": symbol, "subscribe": 1, "style": "candles", "granularity": 60}
            ws.send(json.dumps(sub_req))

def on_open(ws):
    print("🌐 Connexion au WebSocket Deriv réussie !")
   # send_telegram_msg("🤖 <b>Bot Synthétique Deriv (10 Actifs - 24/7) connecté avec succès !</b>")
    
    # Demander l'historique M1 pour chaque actif
    for symbol in SYMBOLS:
        req = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": 50,
            "end": "latest",
            "style": "candles",
            "granularity": 60
        }
        ws.send(json.dumps(req))
        time.sleep(0.2)

def on_error(ws, error):
    print(f"⚠️ Erreur WebSocket : {error}")

def on_close(ws, close_status_code, close_msg):
    print("🔌 Connexion fermée. Reconnexion automatique dans 5 secondes...")
    time.sleep(5)
    run_bot()

def run_bot():
    ws = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever()

if __name__ == "__main__":
    run_bot()
