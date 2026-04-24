import asyncio
from datetime import datetime
from database.models import SessionLocal, Config, TradeAtivo
from brain.feature_factory import FeatureFactory
from brain.ensemble import AegisEnsemble
from engine.binance_client import AegisBinanceClient
from engine.wallet_manager import WalletManager
from engine.notifier import AegisNotifier
from engine.executor import AegisExecutor


class AegisScanner:

    def __init__(self, binance_client):
        self.be = binance_client

        self.factory = FeatureFactory()
        self.ensemble = AegisEnsemble(self.be)

        self.wallet = WalletManager(
            binance_client=self.be,
            ensemble=self.ensemble
        )

        self.notifier = AegisNotifier()
        self.executor = AegisExecutor(self.be)
        self.valid_symbols = getattr(self.be, "valid_symbols", set())

    async def start_monitoring(self):

        print("🛡️ [AEGIS] Scanner Inteligente Ativo")

        while True:
            session = SessionLocal()

            try:
                cfg = session.query(Config).first()

                if not cfg or not cfg.bot_ligado:
                    await asyncio.sleep(30)
                    continue

                max_ativos = cfg.max_ativos_scan or 30

                ativos_ocupados = {
                    t.symbol for t in session.query(TradeAtivo)
                    .filter_by(modo_teste=cfg.modo_teste).all()
                }

                blacklist = ativos_ocupados

                # 🔥 CORRIGIDO: chamada bloqueante em thread separada
                moedas, btc_var = await asyncio.to_thread(
                    self.be.get_market_data,
                    volume_min=cfg.volume_minimo_24h,
                    btc_protecao_limite=cfg.protecao_btc,
                    blacklist=blacklist,
                    limit=max_ativos
                )

                print(f"\n--- 🔍 SCANNER ---")
                print(f"Ativos recebidos: {len(moedas)}")

                # 🔥 CORRIGIDO: daily stop em thread separada
                daily_ok = await asyncio.to_thread(
                    self.wallet.check_daily_stop,
                    session,
                    cfg.modo_teste
                )

                if not daily_ok:
                    print("🛑 Daily stop atingido, aguardando 5min...")
                    await asyncio.sleep(300)
                    continue

                candidatos = []

                for symbol in moedas:

                    if self.valid_symbols and symbol not in self.valid_symbols:
                        continue

                    # 🔥 CORRIGIDO: get_candles em thread separada
                    candles = await asyncio.to_thread(
                        self.be.get_candles, symbol, '15m', 120
                    )

                    if candles is None or candles.empty:
                        continue

                    try:
                        # 🔥 CORRIGIDO: calculate_indicators em thread separada
                        df = await asyncio.to_thread(
                            self.factory.calculate_indicators, candles
                        )

                        if df is None or df.empty:
                            continue

                        row = df.iloc[-1]

                    except Exception as e:
                        print(f"⚠️ Erro indicadores {symbol}: {e}")
                        continue

                    try:
                        rel_volume = float(row.get("relative_volume", 1))
                        atr = float(row.get("atr", 0))
                        close = float(row.get("close", 0))
                        dist_ema = float(row.get("dist_ema_200", 0))
                        ema_slope = float(row.get("ema200_slope", 0))
                    except:
                        continue

                    if rel_volume < 0.5:
                        continue

                    if close > 0 and atr < close * 0.0015:
                        continue

                    trend_strength = abs(dist_ema) + abs(ema_slope * 1000)
                    volatility = atr / close if close > 0 else 0

                    score = (
                        (rel_volume * 0.5) +
                        (trend_strength * 0.3) +
                        (volatility * 0.2)
                    )

                    candidatos.append((symbol, df, score))

                candidatos.sort(key=lambda x: x[2], reverse=True)
                candidatos = candidatos[:max_ativos]

                print(f"🎯 Após filtro: {len(candidatos)} ativos (limite: {max_ativos})")

                for symbol, df_features, score_base in candidatos:

                    if self.valid_symbols and symbol not in self.valid_symbols:
                        continue

                    # 🔥 CORRIGIDO: can_open_position em thread separada
                    pode_abrir = await asyncio.to_thread(
                        self.wallet.can_open_position, session, cfg.modo_teste
                    )

                    if not pode_abrir:
                        print("⏳ Limite de posições atingido")
                        await asyncio.sleep(300)
                        break

                    veredito = await self.ensemble.evaluate(symbol, df_features.tail(1))

                    print(
                        f"🧠 IA | {symbol} | "
                        f"Decisão: {veredito['decisao']} | "
                        f"Confiança: {veredito['confianca_ia']}% | "
                        f"Regime: {veredito.get('regime')}"
                    )

                    if btc_var <= cfg.protecao_btc and veredito['decisao'] == "BUY":
                        print(f"⚠️ BLOQUEADO LONG por queda do BTC ({btc_var}%)")
                        continue

                    if veredito['decisao'] not in ["BUY", "SELL"]:
                        print(f"❌ {symbol} ignorado | Decisão IA: {veredito['decisao']}")
                        continue

                    limiar = 70

                    if veredito['confianca_ia'] < limiar:
                        print(f"❌ {symbol} ignorado | Confiança baixa ({veredito['confianca_ia']} < {limiar})")
                        continue

                    # 🔥 CORRIGIDO: calculate_position_size em thread separada
                    v_planejado = await asyncio.to_thread(
                        self.wallet.calculate_position_size,
                        session,
                        veredito['multiplicador'],
                        cfg.modo_teste
                    )

                    if v_planejado <= 0:
                        print(f"❌ {symbol} ignorado | Valor inválido para ordem")
                        continue

                    preco = float(df_features['close'].iloc[-1])
                    tipo = "LONG" if veredito['decisao'] == "BUY" else "SHORT"

                    print(f"🎯 ENTRADA {tipo} | {symbol} | Score: {score_base:.2f}")

                    # 🔥 CORRIGIDO: execute_trade em thread separada
                    await asyncio.to_thread(
                        self.executor.execute_trade,
                        symbol=symbol,
                        decisao=veredito['decisao'],
                        confianca=veredito['confianca_ia'],
                        session=session,
                        cfg=cfg,
                        preco_ref=preco
                    )

            except Exception as e:
                print(f"❌ ERRO: {e}")

                try:
                    self.notifier.send_message(str(e))
                except:
                    pass

                if 'session' in locals():
                    session.rollback()

                await asyncio.sleep(60)

            finally:
                if 'session' in locals():
                    session.close()

            await asyncio.sleep(60)