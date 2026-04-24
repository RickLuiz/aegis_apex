import pandas as pd
import numpy as np


class FeatureFactory:
    """A Cozinha do Aegis Alpha: Transforma dados brutos em inteligência."""

    @staticmethod
    def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """
        Recebe um DataFrame com colunas [timestamp, open, high, low, close, volume]
        e retorna o DataFrame recheado de indicadores técnicos.
        """

        # Evita modificar o original
        df = df.copy()

        # =========================
        # VALIDAÇÃO BÁSICA
        # =========================
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"Coluna obrigatória ausente: {col}")

        # Garante tipo numérico
        for col in required_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # =========================
        # 1. RSI
        # =========================
        delta = df['close'].diff()

        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()

        # 🔥 evita divisão por zero
        rs = avg_gain / avg_loss.replace(0, np.nan)

        df['rsi'] = 100 - (100 / (1 + rs))
        df['rsi'] = df['rsi'].fillna(50)  # fallback neutro

        # =========================
        # 2. EMA 200 + DISTÂNCIA
        # =========================
        df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()

        # 🔥 proteção contra divisão por zero
        df['dist_ema_200'] = np.where(
            df['ema_200'] != 0,
            ((df['close'] / df['ema_200']) - 1) * 100,
            0
        )

        # =========================
        # 3. ATR
        # =========================
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()

        ranges = pd.concat([high_low, high_close, low_close], axis=1)

        # 🔥 correção importante aqui
        true_range = ranges.max(axis=1)

        df['atr'] = true_range.rolling(window=14).mean()

        # =========================
        # 4. VOLUME RELATIVO
        # =========================
        df['volume_mean'] = df['volume'].rolling(window=20).mean()

        df['relative_volume'] = np.where(
            df['volume_mean'] != 0,
            df['volume'] / df['volume_mean'],
            1.0
        )

        # =========================
        # EXTRA (já usado no ensemble)
        # =========================
        df['ema200_slope'] = df['ema_200'].diff()

        # =========================
        # LIMPEZA FINAL
        # =========================
        df = df.replace([np.inf, -np.inf], np.nan)

        return df.dropna()

    @staticmethod
    def prepare_for_model(df: pd.DataFrame, features_list: list):
        """
        Prepara os dados no formato exato que o Scikit-Learn/XGBoost exige.
        """

        if df is None or df.empty:
            return None

        try:
            df_clean = df.copy()

            # Garante que todas as features existem
            for col in features_list:
                if col not in df_clean.columns:
                    df_clean[col] = 0.0

            return df_clean[features_list].tail(1)

        except Exception as e:
            print(f"❌ Erro prepare_for_model: {e}")
            return None