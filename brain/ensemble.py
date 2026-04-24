import os
import joblib
import pandas as pd
import warnings
import asyncio

from observers.order_flow import OrderFlowMonitor
from observers.sentiment_check import SentimentCheck

warnings.filterwarnings("ignore", category=UserWarning)


class AegisEnsemble:

    def __init__(self, binance_engine):

        self.modelo_path = os.path.join('brain', 'models', 'modelo_trading.pkl')
        self.feature_names_path = os.path.join('brain', 'models', 'feature_names.pkl')

        try:
            self.modelo_tecnico = joblib.load(self.modelo_path)

            try:
                self.expected_features = joblib.load(self.feature_names_path)
                print(f"🧠 [IA] Modelo carregado ({len(self.expected_features)} features)")
            except:
                self.expected_features = None

        except Exception as e:
            self.modelo_tecnico = None
            self.expected_features = None
            print(f"⚠️ Erro ao carregar modelo: {e}")

        self.flow_monitor = OrderFlowMonitor(binance_engine)
        self.sentiment = SentimentCheck()

        self.valid_symbols = getattr(binance_engine, "valid_symbols", set())

        self.MODO_TESTE = True

    # =========================
    # 🚀 ENTRADA (LONG + SHORT)
    # =========================
    async def evaluate(self, symbol: str, features_df: pd.DataFrame) -> dict:

        if self.valid_symbols and symbol not in self.valid_symbols:
            print(f"⚠️ [IA] Símbolo inválido ignorado: {symbol}")
            return self._skip(symbol, 0, 0, {"status": "NEUTRAL"}, "Símbolo inválido")

        if features_df is None or features_df.empty:
            return self._skip(symbol, 0, 0, {"status": "NEUTRAL"}, "Sem dados")

        prob_long   = 0.0
        prob_short  = 0.0
        prob_neutro = 0.0

        # =========================
        # IA MULTICLASSE
        # =========================
        if self.modelo_tecnico is not None:
            try:
                X_clean = features_df.apply(pd.to_numeric, errors='coerce').fillna(0.0)

                if self.expected_features:
                    X = X_clean.reindex(columns=self.expected_features, fill_value=0.0)
                else:
                    X = X_clean

                X_last = X.iloc[-1:].copy()
                probs = self.modelo_tecnico.predict_proba(X_last)[0]

                # 🔥 modelo multiclasse: [0]=SHORT [1]=NEUTRO [2]=LONG
                if len(probs) == 3:
                    prob_short  = float(probs[0])
                    prob_neutro = float(probs[1])
                    prob_long   = float(probs[2])
                else:
                    # fallback: modelo binário antigo (compatibilidade)
                    prob_long  = float(probs[1])
                    prob_short = 1.0 - prob_long
                    prob_neutro = 0.0

            except Exception as e:
                print(f"❌ IA erro {symbol}: {e}")

        # Sinal direcional limpo: +1 = forte LONG, -1 = forte SHORT
        prob_ia_direcional = prob_long - prob_short  # range: -1 a +1

        # =========================
        # ORDER FLOW
        # =========================
        fluxo = None

        try:
            fluxo = self.flow_monitor.check_pressure(symbol)
        except Exception as e:
            print(f"⚠️ OrderFlow erro {symbol}: {e}")
            fluxo = None

        if isinstance(fluxo, dict):
            imbalance = float(fluxo.get('imbalance', 0.0))
        else:
            imbalance = 0.0

        # =========================
        # SENTIMENTO
        # =========================
        try:
            dados_sentimento = await self.sentiment.get_market_sentiment()
            peso_humor = float(self.sentiment.get_sentiment_weight(dados_sentimento))
        except Exception as e:
            print(f"⚠️ Sentimento erro: {e}")
            dados_sentimento = {"status": "NEUTRAL"}
            peso_humor = 1.0

        peso_humor = max(0.7, min(peso_humor, 1.3))

        # =========================
        # FEATURES
        # =========================
        try:
            row = features_df.iloc[-1]

            rsi        = float(row.get("rsi", 50))
            atr        = float(row.get("atr", 0))
            dist_ema   = float(row.get("dist_ema_200", 0))
            rel_volume = float(row.get("relative_volume", 1))
            close_price = float(row.get("close", 0))
            ema_slope  = float(row.get("ema200_slope", 0))

        except Exception as e:
            print(f"⚠️ Feature erro {symbol}: {e}")
            rsi, atr, dist_ema, rel_volume, close_price, ema_slope = 50, 0, 0, 1, 0, 0

        # =========================
        # REGIME
        # =========================
        regime = "LATERAL"

        if close_price > 0:
            if abs(dist_ema) > 1.2 and abs(ema_slope) > 0.001:
                regime = "TREND"
            elif atr > close_price * 0.04:
                regime = "VOLATILE"

        # =========================
        # NORMALIZAÇÕES
        # =========================
        rsi_norm       = (rsi - 50) / 50          # -1 a +1
        trend_strength = max(min(dist_ema / 3, 1), -1)  # -1 a +1

        # =========================
        # 🔥 SCORE DIRECIONAL
        # prob_ia_direcional já é -1 a +1
        # =========================
        score = (
            (prob_ia_direcional * 0.40) +
            (imbalance          * 0.25) +
            (trend_strength     * 0.20) +
            (rsi_norm           * 0.15)
        )

        score *= peso_humor
        score = max(-1, min(score, 1))

        # =========================
        # THRESHOLD
        # =========================
        threshold = 0.55

        if regime == "TREND":
            threshold = 0.50
        elif regime == "VOLATILE":
            threshold = 0.65

        if rel_volume > 1.5:
            threshold -= 0.05

        if self.MODO_TESTE:
            threshold = 0.30

        # =========================
        # DECISÃO LONG / SHORT
        # =========================
        decisao     = "SKIP"
        multiplicador = 0.0

        if score >= threshold:
            decisao = "BUY"
        elif score <= -threshold:
            decisao = "SELL"

        if decisao != "SKIP":
            forca = abs(score)

            if forca > threshold + 0.20:
                multiplicador = 1.6
            elif forca > threshold + 0.10:
                multiplicador = 1.3
            else:
                multiplicador = 1.0

            multiplicador *= peso_humor

        # =========================
        # LOG
        # =========================
        print(
            f"🧠 IA | {symbol} | "
            f"L={prob_long:.2f} N={prob_neutro:.2f} S={prob_short:.2f} | "
            f"Score={score:.2f} | "
            f"Thr={threshold:.2f} | "
            f"RSI={rsi:.1f} | "
            f"Fluxo={imbalance:.2f} | "
            f"Regime={regime} | "
            f"Decisão={decisao}"
        )

        return {
            "symbol":        symbol,
            "decisao":       decisao,
            "regime":        regime,
            "confianca_ia":  round(max(prob_long, prob_short) * 100, 2),
            "score":         round(score, 4),
            "threshold":     round(threshold, 4),
            "pressao_fluxo": round(imbalance, 4),
            "multiplicador": round(multiplicador, 2),
            "humor_mercado": dados_sentimento.get('status', 'NEUTRAL'),
            "msg":           f"{regime} | score={score:.2f}"
        }

    # =========================
    # SAÍDA
    # =========================
    def evaluate_exit(self, features_df: pd.DataFrame) -> str:

        try:
            row = features_df.iloc[-1]

            rsi        = float(row.get("rsi", 50))
            rel_volume = float(row.get("relative_volume", 1))
            dist_ema   = float(row.get("dist_ema_200", 0))
            ema_slope  = float(row.get("ema200_slope", 0))

            if rsi > 80:
                return "EXAUSTAO"

            if rsi > 70 and rel_volume < 1:
                return "EXAUSTAO"

            if dist_ema > 0 and ema_slope > 0:
                return "CONTINUACAO"

            return "NEUTRO"

        except Exception as e:
            print(f"❌ Erro evaluate_exit: {e}")
            return "NEUTRO"

    # =========================
    # AUX
    # =========================
    def _skip(self, symbol, prob, imbalance, sentimento, motivo):
        return {
            "symbol":        symbol,
            "decisao":       "SKIP",
            "regime":        "SKIP",
            "confianca_ia":  round(prob * 100, 2),
            "score":         0.0,
            "threshold":     0.0,
            "pressao_fluxo": round(imbalance, 4),
            "multiplicador": 0.0,
            "humor_mercado": sentimento.get('status', 'NEUTRAL'),
            "msg":           motivo
        }