import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pandas as pd
import joblib
import time
import os
from datetime import datetime, timezone
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
''
from database.models import SessionLocal, HistoricoFeature, init_db
from engine.binance_client import AegisBinanceClient
from brain.feature_factory import FeatureFactory

from xgboost import XGBClassifier
from sklearn.metrics import classification_report


# =========================
# 🎯 TARGET DIRECIONAL (LONG / NEUTRO / SHORT)
# =========================
def create_trade_target(df, tp_atr=1.2, sl_atr=0.8, lookahead=12):
    """
    Retorna target triclasse:
      0 = SHORT  (preço caiu e bateu TP short antes do SL short)
      1 = NEUTRO (sem direção clara no período)
      2 = LONG   (preço subiu e bateu TP long antes do SL long)
    """
    targets = []

    for i in range(len(df)):
        if i + lookahead >= len(df):
            targets.append(None)
            continue

        entry = df.iloc[i]['close']
        atr   = df.iloc[i]['atr']

        if atr == 0 or entry == 0:
            targets.append(None)
            continue

        tp_long  = entry + (atr * tp_atr)
        sl_long  = entry - (atr * sl_atr)
        tp_short = entry - (atr * tp_atr)
        sl_short = entry + (atr * sl_atr)

        future = df.iloc[i+1:i+1+lookahead]
        result = 1  # neutro por padrão

        for _, row in future.iterrows():
            long_win   = row['high'] >= tp_long
            long_loss  = row['low']  <= sl_long
            short_win  = row['low']  <= tp_short
            short_loss = row['high'] >= sl_short

            if long_win and not short_win:
                result = 2  # LONG claro
                break
            if short_win and not long_win:
                result = 0  # SHORT claro
                break
            if long_loss or short_loss:
                result = 1  # neutro / stop
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

    symbols = [
        'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ADAUSDT',
        'XRPUSDT', 'DOTUSDT', 'LINKUSDT', 'AVAXUSDT', 'MATICUSDT',
        'DOGEUSDT', 'SHIBUSDT', 'PEPEUSDT', 'WIFUSDT', 'FLOKIUSDT',
        'BONKUSDT', '1000CHEEMSUSDT', '1000CATUSDT', 'MEMEUSDT', 'BOMEUSDT',
        'NEARUSDT', 'RNDRUSDT', 'FETUSDT', 'AGIXUSDT', 'TAOUSDT',
        'ARUSDT', 'THETAUSDT', 'FILUSDT', 'GRTUSDT', 'LDOUSDT',
        'SUIUSDT', 'APTUSDT', 'OPUSDT', 'ARBUSDT', 'TIAUSDT',
        'SEIUSDT', 'INJUSDT', 'STXUSDT', 'FTMUSDT', 'AAVEUSDT',
        'FUNUSDT', 'FRAXUSDT', 'ENJUSDT', 'GALAUSDT', 'CHZUSDT',
        'CRVUSDT', 'DYDXUSDT', 'USTCUSDT', 'ZKPUSDT'
    ]

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
                    if k not in ['timestamp', 'close', 'volume', 'close_time'] and v is not None
                }

                stmt = pg_insert(HistoricoFeature).values(
                    symbol=symbol,
                    timestamp=ts,
                    close=float(row['close']),
                    volume=float(row['volume']),
                    features=features
                ).on_conflict_do_nothing(index_elements=['symbol', 'timestamp'])

                session.execute(stmt)

            session.commit()
            print(f"✅ {symbol} ingerido")

        except Exception as e:
            print(f"❌ Erro {symbol}: {e}")
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

    df = pd.DataFrame(data).sort_values(['symbol', 'timestamp']).reset_index(drop=True)

    # =========================
    # FEATURES EXTRAS
    # =========================
    df['dist_ema_200'] = (df['close'] - df['ema_200']) / df['ema_200']
    df['ema200_slope'] = (df['ema_200'] - df.groupby('symbol')['ema_200'].shift(10)) / df['ema_200']
    df['atr_norm'] = df['atr'] / df['close']

    # =========================
    # 🎯 TARGET DIRECIONAL
    # =========================
    print("🎯 Criando target direcional (SHORT=0 / NEUTRO=1 / LONG=2)...")

    df['target'] = df.groupby('symbol').apply(
        lambda x: pd.Series(
            create_trade_target(x, tp_atr=0.8, sl_atr=0.8, lookahead=20),
            index=x.index
        )
    ).reset_index(level=0, drop=True)

    df.dropna(subset=['target'], inplace=True)
    df['target'] = df['target'].astype(int)

    dist = df['target'].value_counts().sort_index()
    print(f"📊 Distribuição: SHORT={dist.get(0,0)} | NEUTRO={dist.get(1,0)} | LONG={dist.get(2,0)}")

    # =========================
    # SPLIT TEMPORAL
    # =========================
    df = df.sort_values('timestamp')
    split_idx = int(len(df) * 0.8)

    train_df = df.iloc[:split_idx]
    test_df  = df.iloc[split_idx:]

    feature_cols = [c for c in df.columns if c not in ['symbol', 'timestamp', 'close', 'target']]

    X_train = train_df[feature_cols]
    y_train = train_df['target']
    X_test  = test_df[feature_cols]
    y_test  = test_df['target']

    # =========================
    # 🔥 MODELO MULTICLASSE COM BALANCEAMENTO MODERADO
    # =========================
    print("🤖 Treinando modelo multiclasse (SHORT / NEUTRO / LONG)...")

    contagem = y_train.value_counts()
    total = len(y_train)

    # 🔥 NEUTRO valorizado para não suprimi-lo, SHORT/LONG moderados
    pesos = {
        0: total / (3 * contagem.get(0, 1)) * 0.75,  # SHORT — moderado
        1: total / (3 * contagem.get(1, 1)) * 1.50,  # NEUTRO — valoriza mais
        2: total / (3 * contagem.get(2, 1)) * 0.75,  # LONG — moderado
    }
    sample_weights = y_train.map(pesos).values

    print(f"⚖️ Pesos: SHORT={pesos[0]:.2f} | NEUTRO={pesos[1]:.2f} | LONG={pesos[2]:.2f}")

    model = XGBClassifier(
        n_estimators=500,
        max_depth=4,           # 🔥 era 5, reduz overfitting
        learning_rate=0.05,
        objective='multi:softprob',
        num_class=3,
        n_jobs=-1,
        random_state=42,
        eval_metric='mlogloss',
        subsample=0.7,         # 🔥 era 0.8, mais conservador
        colsample_bytree=0.7,  # 🔥 era 0.8
        min_child_weight=10,   # 🔥 era 5, exige mais exemplos por folha
        gamma=2.0              # 🔥 novo: penaliza splits desnecessários
    )

    model.fit(X_train, y_train, sample_weight=sample_weights)

    probs = model.predict_proba(X_test)
    y_pred = probs.argmax(axis=1)

    print("\n📈 RELATÓRIO FINAL:")
    print(classification_report(y_test, y_pred, target_names=['SHORT', 'NEUTRO', 'LONG']))

    # =========================
    # TREINO FINAL (100% dos dados)
    # =========================
    print("🔁 Retreinando com 100% dos dados...")
    X_full = df[feature_cols]
    y_full = df['target']

    contagem_full = y_full.value_counts()
    total_full = len(y_full)
    pesos_full = {
        0: total_full / (3 * contagem_full.get(0, 1)) * 0.75,
        1: total_full / (3 * contagem_full.get(1, 1)) * 1.50,
        2: total_full / (3 * contagem_full.get(2, 1)) * 0.75,
    }
    sample_weights_full = y_full.map(pesos_full).values

    model.fit(X_full, y_full, sample_weight=sample_weights_full)

    os.makedirs('brain/models', exist_ok=True)
    joblib.dump(model, 'brain/models/modelo_trading.pkl')
    joblib.dump(feature_cols, 'brain/models/feature_names.pkl')

    print("✅ Modelo multiclasse salvo com sucesso!")
    session.close()

    init_db()
    be = AegisBinanceClient()
    factory = FeatureFactory()
    session = SessionLocal()

    symbols = [
        'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ADAUSDT',
        'XRPUSDT', 'DOTUSDT', 'LINKUSDT', 'AVAXUSDT', 'MATICUSDT',
        'DOGEUSDT', 'SHIBUSDT', 'PEPEUSDT', 'WIFUSDT', 'FLOKIUSDT',
        'BONKUSDT', '1000CHEEMSUSDT', '1000CATUSDT', 'MEMEUSDT', 'BOMEUSDT',
        'NEARUSDT', 'RNDRUSDT', 'FETUSDT', 'AGIXUSDT', 'TAOUSDT',
        'ARUSDT', 'THETAUSDT', 'FILUSDT', 'GRTUSDT', 'LDOUSDT',
        'SUIUSDT', 'APTUSDT', 'OPUSDT', 'ARBUSDT', 'TIAUSDT',
        'SEIUSDT', 'INJUSDT', 'STXUSDT', 'FTMUSDT', 'AAVEUSDT',
        'FUNUSDT', 'FRAXUSDT', 'ENJUSDT', 'GALAUSDT', 'CHZUSDT',
        'CRVUSDT', 'DYDXUSDT', 'USTCUSDT', 'ZKPUSDT'
    ]

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
                    if k not in ['timestamp', 'close', 'volume', 'close_time'] and v is not None
                }

                stmt = pg_insert(HistoricoFeature).values(
                    symbol=symbol,
                    timestamp=ts,
                    close=float(row['close']),
                    volume=float(row['volume']),
                    features=features
                ).on_conflict_do_nothing(index_elements=['symbol', 'timestamp'])

                session.execute(stmt)

            session.commit()
            print(f"✅ {symbol} ingerido")

        except Exception as e:
            print(f"❌ Erro {symbol}: {e}")
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

    df = pd.DataFrame(data).sort_values(['symbol', 'timestamp']).reset_index(drop=True)

    # =========================
    # FEATURES EXTRAS
    # =========================
    df['dist_ema_200'] = (df['close'] - df['ema_200']) / df['ema_200']
    df['ema200_slope'] = (df['ema_200'] - df.groupby('symbol')['ema_200'].shift(10)) / df['ema_200']
    df['atr_norm'] = df['atr'] / df['close']

    # =========================
    # 🎯 TARGET DIRECIONAL
    # =========================
    print("🎯 Criando target direcional (SHORT=0 / NEUTRO=1 / LONG=2)...")

    df['target'] = df.groupby('symbol').apply(
        lambda x: pd.Series(create_trade_target(x), index=x.index)
    ).reset_index(level=0, drop=True)

    df.dropna(subset=['target'], inplace=True)
    df['target'] = df['target'].astype(int)

    dist = df['target'].value_counts().sort_index()
    print(f"📊 Distribuição: SHORT={dist.get(0,0)} | NEUTRO={dist.get(1,0)} | LONG={dist.get(2,0)}")

    # =========================
    # SPLIT TEMPORAL
    # =========================
    df = df.sort_values('timestamp')
    split_idx = int(len(df) * 0.8)

    train_df = df.iloc[:split_idx]
    test_df  = df.iloc[split_idx:]

    feature_cols = [c for c in df.columns if c not in ['symbol', 'timestamp', 'close', 'target']]

    X_train = train_df[feature_cols]
    y_train = train_df['target']
    X_test  = test_df[feature_cols]
    y_test  = test_df['target']

    # =========================
    # 🔥 MODELO MULTICLASSE COM BALANCEAMENTO
    # =========================
    print("🤖 Treinando modelo multiclasse (SHORT / NEUTRO / LONG)...")

    contagem = y_train.value_counts()
    total = len(y_train)
    pesos = {cls: total / (3 * cnt) for cls, cnt in contagem.items()}
    sample_weights = y_train.map(pesos).values

    print(f"⚖️ Pesos: SHORT={pesos.get(0,1):.2f} | NEUTRO={pesos.get(1,1):.2f} | LONG={pesos.get(2,1):.2f}")

    model = XGBClassifier(
        n_estimators=500,
        max_depth=4,           # 🔥 era 5, reduz overfitting
        learning_rate=0.05,
        objective='multi:softprob',
        num_class=3,
        n_jobs=-1,
        random_state=42,
        eval_metric='mlogloss',
        subsample=0.7,         # 🔥 era 0.8, mais conservador
        colsample_bytree=0.7,  # 🔥 era 0.8
        min_child_weight=10,   # 🔥 era 5, exige mais exemplos por folha
        gamma=2.0              # 🔥 novo: penaliza splits desnecessários
    )

    model.fit(X_train, y_train, sample_weight=sample_weights)

    probs = model.predict_proba(X_test)
    y_pred = probs.argmax(axis=1)

    print("\n📈 RELATÓRIO FINAL:")
    print(classification_report(y_test, y_pred, target_names=['SHORT', 'NEUTRO', 'LONG']))

    # =========================
    # TREINO FINAL (100% dos dados)
    # =========================
    print("🔁 Retreinando com 100% dos dados...")
    X_full = df[feature_cols]
    y_full = df['target']

    contagem_full = y_full.value_counts()
    total_full = len(y_full)
    pesos_full = {cls: total_full / (3 * cnt) for cls, cnt in contagem_full.items()}
    sample_weights_full = y_full.map(pesos_full).values

    model.fit(X_full, y_full, sample_weight=sample_weights_full)

    os.makedirs('brain/models', exist_ok=True)
    joblib.dump(model, 'brain/models/modelo_trading.pkl')
    joblib.dump(feature_cols, 'brain/models/feature_names.pkl')

    print("✅ Modelo multiclasse salvo com sucesso!")

    session.close()


if __name__ == "__main__":
    train_now()