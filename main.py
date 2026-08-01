import os
import json
import time
import requests
import pandas as pd
import numpy as np
import websocket

# =====================================================================
# CONFIGURATION TELEGRAM & DERIV
# =====================================================================
TELEGRAM_TOKEN ="8900570872:AAGHVeWBDobqPqJ4D_b74npZ_I89uMY5-_A"
TELEGRAM_CHAT_ID ="6365221307"

DERIV_APP_ID = 1089
WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

SYMBOLS = {
    "R_10": "Volatility 10 Index",
    "R_25": "Volatility 25 Index",
    "R_50": "Volatility 50 Index",
    "R_75": "Volatility 75 Index",
    "R_100": "Volatility 100 Index",
    "1HZ10V": "Volatility 10 (1s) Index",
    "1HZ25V": "Volatility 25 (1s) Index",
    "1HZ50V": "Volatility 50 (1s) Index",
    "1HZ75V": "Volatility 75 (1s) Index",
    "1HZ100V": "Volatility 100 (1s) Index"
}

# Granularités nécessaires à la cascade : Daily, H4, H1, M15, M5, M3, M1
GRANULARITIES = [86400, 14400, 3600, 900, 300, 180, 60]

# Anti-doublon (même principe que sur les autres bots)
sent_signals = set()
MAX_SENT_SIGNALS = 3000

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Erreur d'envoi Telegram: {e}")

# =====================================================================
# INDICATEURS TECHNIQUES (EMA & ATR) — inchangés
# =====================================================================
def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(period).mean()

# =====================================================================
# ÉVALUATEUR FRACTAL POUR LES 4 CONFIGURATIONS — LOGIQUE INCHANGÉE
# (identique à la version validée : Swing, Intraday, Scalping)
# =====================================================================
def evaluate_fractal_layer(df_htf, df_itf, df_ltf, level_label):
    """
    Évalue la structure selon la chaîne de fractalité exacte :
    - df_htf : Structure Externe (Biais & Bornes)
    - df_itf : Structure Interne (BOS, Sweep, Turtle Soup)
    - df_ltf : Exécution (Zone OTE 61.8%-79%, SL Jambe protégée, EMA)
    """
    if df_htf.empty or df_itf.empty or df_ltf.empty:
        return None, None, None, None, None, None

    # 1. STRUCTURE EXTERNE (HTF)
    htf_high = df_htf['high'].iloc[-5:].max()
    htf_low = df_htf['low'].iloc[-5:].min()
    htf_trend = "BUY" if df_htf['close'].iloc[-1] > df_htf['open'].iloc[-1] else "SELL"

    # 2. STRUCTURE INTERNE (ITF) - Sweep / Turtle Soup
    itf_high = df_itf['high'].iloc[-10:].max()
    itf_low = df_itf['low'].iloc[-10:].min()
    itf_close = df_itf['close'].iloc[-1]

    sweep_buy = (df_itf['low'].iloc[-2] < itf_low) and (itf_close > itf_low)
    sweep_sell = (df_itf['high'].iloc[-2] > itf_high) and (itf_close < itf_high)

    # 3. EXÉCUTION LTF - Zone OTE (61.8% - 79%) & EMA
    ltf_low = df_ltf['low'].iloc[-15:].min()
    ltf_high = df_ltf['high'].iloc[-15:].max()
    leg_height = ltf_high - ltf_low
    current_price = df_ltf['close'].iloc[-1]
    candle_epoch = df_ltf['epoch'].iloc[-1] if 'epoch' in df_ltf.columns else str(df_ltf.index[-1])

    if leg_height == 0:
        return None, None, None, None, None, None

    ote_buy_min, ote_buy_max = ltf_high - (leg_height * 0.79), ltf_high - (leg_height * 0.618)
    ote_sell_min, ote_sell_max = ltf_low + (leg_height * 0.618), ltf_low + (leg_height * 0.79)

    in_ote_buy = ote_buy_min <= current_price <= ote_buy_max
    in_ote_sell = ote_sell_min <= current_price <= ote_sell_max

    ema15 = calculate_ema(df_ltf['close'], 15).iloc[-1]
    ema40 = calculate_ema(df_ltf['close'], 40).iloc[-1]
    ema_bullish = ema15 > ema40
    ema_bearish = ema15 < ema40

    # CONFIG 4 : MULTI-CONFLUENCE ULTIME (SMC + CRT + GANN + EMA)
    if htf_trend == "BUY" and sweep_buy and in_ote_buy and ema_bullish:
        return "BUY", f"CONFIG 4 (Multi-Confluence [{level_label}])", current_price, ltf_low, htf_high, candle_epoch
    elif htf_trend == "SELL" and sweep_sell and in_ote_sell and ema_bearish:
        return "SELL", f"CONFIG 4 (Multi-Confluence [{level_label}])", current_price, ltf_high, htf_low, candle_epoch

    # CONFIG 1 : SMC FRACTAL (BOS HTF/ITF -> Retest OTE LTF)
    if htf_trend == "BUY" and in_ote_buy:
        return "BUY", f"CONFIG 1 (SMC OTE Retest [{level_label}])", current_price, ltf_low, htf_high, candle_epoch
    elif htf_trend == "SELL" and in_ote_sell:
        return "SELL", f"CONFIG 1 (SMC OTE Retest [{level_label}])", current_price, ltf_high, htf_low, candle_epoch

    # CONFIG 2 : CRT (Range HTF -> Sweep ITF -> Entrée LTF)
    if sweep_buy:
        return "BUY", f"CONFIG 2 (CRT Sweep [{level_label}])", current_price, df_itf['low'].iloc[-2], htf_high, candle_epoch
    elif sweep_sell:
        return "SELL", f"CONFIG 2 (CRT Sweep [{level_label}])", current_price, df_itf['high'].iloc[-2], htf_low, candle_epoch

    # CONFIG 3 : GANN / NIVEAUX DE PRIX & EMA
    if ema_bullish and in_ote_buy:
        return "BUY", f"CONFIG 3 (Gann & EMA [{level_label}])", current_price, ltf_low, ltf_high + (leg_height * 1.5), candle_epoch
    elif ema_bearish and in_ote_sell:
        return "SELL", f"CONFIG 3 (Gann & EMA [{level_label}])", current_price, ltf_high, ltf_low - (leg_height * 1.5), candle_epoch

    return None, None, None, None, None, None

def analyze_symbol_cascade(symbol, dfs_by_gran):
    """
    Exécute le scan en cascade sur les 3 paires de fractalités strictes :
    1. Swing : Daily (HTF) -> H1 (ITF) -> M5 (LTF)
    2. Intraday : H4 (HTF) -> M15 (ITF) -> M3 (LTF)
    3. Scalping : H1 (HTF) -> M5 (ITF) -> M1 (LTF)
    """
    df_d1 = dfs_by_gran.get(86400, pd.DataFrame())
    df_h4 = dfs_by_gran.get(14400, pd.DataFrame())
    df_h1 = dfs_by_gran.get(3600, pd.DataFrame())
    df_m15 = dfs_by_gran.get(900, pd.DataFrame())
    df_m5 = dfs_by_gran.get(300, pd.DataFrame())
    df_m3 = dfs_by_gran.get(180, pd.DataFrame())
    df_m1 = dfs_by_gran.get(60, pd.DataFrame())

    res = evaluate_fractal_layer(df_d1, df_h1, df_m5, "SWING: Daily/H1/M5")
    if res[0]:
        return res

    res = evaluate_fractal_layer(df_h4, df_m15, df_m3, "INTRADAY: H4/M15/M3")
    if res[0]:
        return res

    res = evaluate_fractal_layer(df_h1, df_m5, df_m1, "SCALPING: H1/M5/M1")
    if res[0]:
        return res

    return None, None, None, None, None, None

# =====================================================================
# TÉLÉCHARGEMENT DES BOUGIES — connexion neuve à chaque cycle
# (une connexion neuve par cycle = reconnexion automatique implicite :
# une coupure ne fait échouer qu'un cycle, jamais tout le process)
# =====================================================================
def fetch_multi_tf_candles(ws, symbol, count=50):
    result = {}
    for gran in GRANULARITIES:
        req = {
            "ticks_history": symbol,
            "count": count,
            "end": "latest",
            "style": "candles",
            "granularity": gran
        }
        try:
            ws.send(json.dumps(req))
            response = json.loads(ws.recv())
            if "candles" in response and response["candles"]:
                df = pd.DataFrame(response["candles"])
                # Sécurité : Deriv peut renvoyer les OHLC en texte selon l'endpoint
                for col in ["open", "high", "low", "close"]:
                    if col in df.columns:
                        df[col] = df[col].astype(float)
                result[gran] = df
            else:
                result[gran] = pd.DataFrame()
        except Exception as e:
            print(f"Erreur granularité {gran} pour {symbol}: {e}")
            result[gran] = pd.DataFrame()
        time.sleep(0.2)  # éviter de saturer l'API Deriv
    return result

# =====================================================================
# BOUCLE PRINCIPALE — remplace l'ancien modèle événementiel one-shot
# =====================================================================
def run_bot():
    send_telegram_msg("🤖 <b>Bot Deriv (Cascade Fractale) démarré et opérationnel !</b>")

    while True:
        try:
            ws = websocket.create_connection(WS_URL, timeout=15)

            for symbol, asset_name in SYMBOLS.items():
                try:
                    dfs_by_gran = fetch_multi_tf_candles(ws, symbol)
                    direction, config, price, sl, tp, candle_epoch = analyze_symbol_cascade(symbol, dfs_by_gran)

                    if direction:
                        signal_id = f"{symbol}_{config}_{direction}_{candle_epoch}"
                        if signal_id not in sent_signals:
                            sent_signals.add(signal_id)
                            if len(sent_signals) > MAX_SENT_SIGNALS:
                                sent_signals.clear()

                            msg = (
                                f"🚨 <b>SIGNAL DE TRADING DERIV</b> 🚨\n\n"
                                f"📌 <b>Actif :</b> {asset_name}\n"
                                f"📈 <b>Direction :</b> {direction}\n"
                                f"⚙️ <b>Stratégie :</b> {config}\n\n"
                                f"🎯 <b>Prix d'entrée :</b> {price:.4f}\n"
                                f"🛑 <b>Stop Loss :</b> {sl:.4f}\n"
                                f"🎯 <b>Take Profit :</b> {tp:.4f}"
                            )
                            send_telegram_msg(msg)
                except Exception as e:
                    print(f"Erreur analyse {symbol}: {e}")
                    continue

            ws.close()

        except Exception as e:
            print(f"Erreur de connexion WebSocket Deriv: {e}")

        time.sleep(180)  # Rafraîchissement toutes les 3 minutes, même rythme que les autres bots

if __name__ == "__main__":
    while True:
        try:
            run_bot()
        except Exception as e:
            print(f"Erreur fatale, redémarrage dans 30s: {e}")
            time.sleep(30)
