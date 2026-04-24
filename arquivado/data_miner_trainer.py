import pandas as pd
import joblib
import time
import os
from datetime import datetime, timezone
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.models import SessionLocal, HistoricoFeature, init_db
from engine.binance_client import AegisBinanceClient
from brain.feature_factory import FeatureFactory

from xgboost import XGBClassifier
from sklearn.metrics import classification_report


# =========================
# 🎯 TARGET PROFISSIONAL
# =========================
def create_trade_target(df, tp_atr=1.2, sl_atr=0.8, lookahead=12):

    targets = []

    for i in range(len(df)):
        if i + lookahead >= len(df):
            targets.append(None)
            continue

        entry = df.iloc[i]['close']
        atr = df.iloc[i]['atr']

        if atr == 0 or entry == 0:
            targets.append(None)
            continue

        tp = entry + (atr * tp_atr)
        sl = entry - (atr * sl_atr)

        future = df.iloc[i+1:i+1+lookahead]

        result = None

        for _, row in future.iterrows():
            if row['high'] >= tp:
                result = 1
                break
            if row['low'] <= sl:
                result = 0
                break

        targets.append(result)

    return targets


# =========================
# 🚀 PIPELINE PRINCIPAL
# =========================
def train_now(dias_maximos=90):

    init_db()
    be = AegisBinanceClient()
    factory = FeatureFactory()
    session = SessionLocal()

    symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ADAUSDT', 'XRPUSDT', 'DOTUSDT', 'LINKUSDT', 'AVAXUSDT', 'MATICUSDT',
               'DOGEUSDT', 'SHIBUSDT', 'PEPEUSDT', 'WIFUSDT', 'FLOKIUSDT', 'BONKUSDT', '1000CHEEMSUSDT', '1000CATUSDT', 'MEMEUSDT', 'BOMEUSDT',
               'NEARUSDT', 'RNDRUSDT', 'FETUSDT', 'AGIXUSDT', 'TAOUSDT', 'ARUSDT', 'THETAUSDT', 'FILUSDT', 'GRTUSDT', 'LDOUSDT',
               'SUIUSDT', 'APTUSDT', 'OPUSDT', 'ARBUSDT', 'TIAUSDT', 'SEIUSDT', 'INJUSDT', 'STXUSDT', 'FTMUSDT', 'AAVEUSDT',
               'FUNUSDT', 'FRAXUSDT', 'ENJUSDT', 'GALAUSDT', 'CHZUSDT', 'CRVUSDT', 'DYDXUSDT', 'USTCUSDT', 'ZKPUSDT']

    print("🚀 [MINER] Iniciando ingestão...")

    # =========================
    # INGESTÃO
    # =========================
    for symbol in symbols:
        try:
            last_entry = session.query(func.max(HistoricoFeature.timestamp)).filter(
                HistoricoFeature.symbol == symbol
            ).scalar()

            agora_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            if last_entry:
                start_ts = int(last_entry.replace(tzinfo=timezone.utc).timestamp() * 1000) + 1
            else:
                start_ts = agora_ms - (dias_maximos * 86400000)

            data_all = []
            current = start_ts

            while current < (agora_ms - 900000):
                df_temp = be.get_candles(symbol, '15m', limit=1000, startTime=current)
                if df_temp.empty:
                    break

                data_all.append(df_temp)

                new_max = df_temp['timestamp'].max()
                if new_max <= current:
                    break

                current = int(new_max) + 1
                time.sleep(0.1)

            if not data_all:
                continue

            df_total = pd.concat(data_all).drop_duplicates('timestamp').sort_values('timestamp')
            df_feat = factory.calculate_indicators(df_total)

            for _, row in df_feat.iterrows():
                ts = pd.to_datetime(row['timestamp'], unit='ms')

                features = {
                    k: float(v)
                    for k, v in row.items()
                    if k not in ['timestamp','close','volume','close_time'] and v is not None
                }

                stmt = pg_insert(HistoricoFeature).values(
                    symbol=symbol,
                    timestamp=ts,
                    close=float(row['close']),
                    volume=float(row['volume']),
                    features=features
                ).on_conflict_do_nothing(index_elements=['symbol','timestamp'])

                session.execute(stmt)

            session.commit()

        except Exception as e:
            print(f"Erro {symbol}: {e}")
            session.rollback()

    # =========================
    # DATASET
    # =========================
    print("🧠 Montando dataset...")

    rows = session.query(HistoricoFeature).all()

    data = []
    for r in rows:
        base = {
            "symbol": r.symbol,
            "timestamp": r.timestamp,
            "close": float(r.close),
            "volume": float(r.volume)
        }

        if isinstance(r.features, dict):
            base.update({k: float(v) for k, v in r.features.items() if v is not None})

        data.append(base)

    df = pd.DataFrame(data).sort_values(['symbol','timestamp']).reset_index(drop=True)

    # =========================
    # FEATURES EXTRAS
    # =========================
    df['dist_ema_200'] = (df['close'] - df['ema_200']) / df['ema_200']
    df['ema200_slope'] = (df['ema_200'] - df.groupby('symbol')['ema_200'].shift(10)) / df['ema_200']
    df['atr_norm'] = df['atr'] / df['close']

    # =========================
    # 🎯 TARGET REAL
    # =========================
    print("🎯 Criando target TP/SL...")

    df['target'] = df.groupby('symbol').apply(
        lambda x: pd.Series(create_trade_target(x), index=x.index)
    ).reset_index(level=0, drop=True)

    df.dropna(subset=['target'], inplace=True)
    df['target'] = df['target'].astype(int)

    # =========================
    # SPLIT TEMPORAL
    # =========================
    df = df.sort_values('timestamp')

    split_idx = int(len(df) * 0.8)

    train_df = df.iloc[:split_idx]
    test_df = df.iloc[split_idx:]

    X_train = train_df.drop(columns=['symbol','timestamp','close','target'], errors='ignore')
    y_train = train_df['target']

    X_test = test_df.drop(columns=['symbol','timestamp','close','target'], errors='ignore')
    y_test = test_df['target']

    # =========================
    # PESO DE CLASSE
    # =========================
    neg = (y_train == 0).sum()
    pos = (y_train == 1).sum()

    scale_pos_weight = neg / pos if pos > 0 else 1

    print(f"⚖️ scale_pos_weight: {scale_pos_weight:.2f}")

    # =========================
    # MODELO
    # =========================
    model = XGBClassifier(
        n_estimators=400,
        max_depth=6,
        learning_rate=0.05,
        scale_pos_weight=scale_pos_weight,
        n_jobs=-1,
        random_state=42
    )

    model.fit(X_train, y_train)

    probs = model.predict_proba(X_test)[:, 1]
    y_pred = (probs > 0.50).astype(int)

    print("\n📈 RELATÓRIO FINAL:")
    print(classification_report(y_test, y_pred))

    # =========================
    # TREINO FINAL
    # =========================
    X_full = df.drop(columns=['symbol','timestamp','close','target'], errors='ignore')
    y_full = df['target']

    model.fit(X_full, y_full)

    os.makedirs('brain/models', exist_ok=True)
    joblib.dump(model, 'brain/models/modelo_trading.pkl')
    joblib.dump(X_full.columns.tolist(), 'brain/models/feature_names.pkl')

    print("✅ Modelo salvo com sucesso!")

    session.close()


if __name__ == "__main__":
    train_now()