import asyncio
from datetime import datetime
from database.models import SessionLocal, Config, TradeAtivo, HistoricoTrade
from engine.notifier import AegisNotifier
from engine.wallet_manager import WalletManager
from brain.ensemble import AegisEnsemble
from brain.feature_factory import FeatureFactory


class AegisExecutor:
    def __init__(self, binance_client):
        self.be = binance_client
        self.notifier = AegisNotifier()
        self.ensemble = AegisEnsemble(self.be)

        self.wallet = WalletManager(
            binance_client=self.be,
            ensemble=self.ensemble
        )

        self.factory = FeatureFactory()
        self.last_notified_stop = {}

    # =========================
    # 🔥 EXECUÇÃO DIRETA (scanner chama)
    # =========================
    def execute_trade(self, symbol, decisao, confianca, session, cfg, preco_ref):

        if not self.wallet.can_open_position(session, modo_teste=cfg.modo_teste):
            return

        preco_atual = self.be.get_current_price(symbol)
        if not preco_atual:
            return

        if decisao == "BUY":
            side = "LONG"
            order_side = "BUY"
        elif decisao == "SELL":
            side = "SHORT"
            order_side = "SELL"
        else:
            return

        valor_final = self.wallet.calculate_position_size(
            session,
            multiplicador=1.0,
            modo_teste=cfg.modo_teste
        )

        if valor_final <= 0:
            return

        rules = self.be.get_symbol_rules(symbol)
        min_qty = rules["min_qty"]
        min_notional = rules["min_notional"]

        valor_minimo_real = min_qty * preco_atual

        if valor_final < valor_minimo_real:
            print(f"🚫 Trade pequeno demais ({symbol}) | precisa >= {valor_minimo_real:.2f}")
            return

        if valor_final < min_notional:
            print(f"🚫 Notional baixo ({symbol}) | {valor_final:.2f} < {min_notional}")
            return

        raw_qty = valor_final / preco_atual
        qty_ajustada = self.be.adjust_quantity(symbol, raw_qty)

        if qty_ajustada <= 0:
            print(f"🚫 BLOQUEADO (qty inválida) {symbol} | valor={valor_final} price={preco_atual}")
            return

        # 🔥 place_order recebe valor em USDT na abertura
        resultado = self.be.place_order(
            symbol=symbol,
            side=order_side,
            amount=valor_final,
            modo_teste=cfg.modo_teste
        )

        if not resultado.get("sucesso"):
            return

        preco_execucao = resultado.get("preco_execucao") or preco_atual
        qty_executada = resultado.get("quantidade") or qty_ajustada
        valor_pago = qty_executada * preco_execucao

        novo_trade = TradeAtivo(
            symbol=symbol,
            side=side,
            quantidade=qty_executada,
            preco_entrada=preco_execucao,
            valor_pago=valor_pago,
            maior_preco_atingido=preco_execucao,
            trailing_percentual=cfg.trailing_stop_percentual / 100,
            parcial_realizada=False,
            modo_teste=cfg.modo_teste
        )

        if cfg.modo_teste:
            self.wallet.update_test_balance(session, valor_pago, "SUBTRAIR")

        emoji_conf = "🔥" if confianca >= 70 else "⚡" if confianca >= 60 else "⚠️"

        self.notifier.send_message(
            f"🚀 ENTRADA {side}\n"
            f"{symbol}\n\n"
            f"💰 Valor: ${valor_pago:.2f}\n"
            f"📍 Preço: {preco_execucao:.4f}\n"
            f"📦 Quantidade: {qty_executada:.6f}\n"
            f"{emoji_conf} Confiança IA: {confianca:.2f}%"
        )

        session.add(novo_trade)
        session.commit()

    # =========================
    # 🔄 LOOP PRINCIPAL
    # =========================
    async def run_execution_loop(self):
        print("🎯 [EXECUTOR] Braço de execução do Aegis Alpha ativo.")

        while True:
            session = SessionLocal()

            try:
                cfg = session.query(Config).first()

                if not cfg or not cfg.bot_ligado:
                    await asyncio.sleep(10)
                    continue

                trades = session.query(TradeAtivo).all()

                for trade in trades:

                    # 🔥 ignora posições com quantidade inválida
                    if trade.quantidade <= 0:
                        print(f"⚠️ Quantidade inválida em {trade.symbol} ({trade.quantidade}), ignorando.")
                        continue

                    preco_atual = await asyncio.to_thread(
                        self.be.get_current_price, trade.symbol
                    )
                    if not preco_atual:
                        continue

                    candles = await asyncio.to_thread(
                        self.be.get_candles, trade.symbol, '15m', 100
                    )
                    if candles is None or candles.empty:
                        continue

                    df_features = await asyncio.to_thread(
                        self.factory.calculate_indicators, candles
                    )
                    if df_features is None or df_features.empty:
                        continue

                    acao = await self.wallet.manage_exit(
                        trade, df_features, preco_atual, db=session
                    )

                    if acao == "HOLD":
                        continue

                    exit_side = 'SELL' if trade.side == "LONG" else 'BUY'

                    # =========================
                    # 🔄 PARCIAL
                    # =========================
                    if acao == "CLOSE_PARTIAL":

                        quantidade_venda = trade.quantidade * 0.5
                        if quantidade_venda <= 0:
                            continue

                        # 🔥 CORRIGIDO: converte quantidade para valor USDT antes de passar ao place_order
                        valor_venda = quantidade_venda * preco_atual

                        resultado = await asyncio.to_thread(
                            self.be.place_order,
                            trade.symbol, exit_side, valor_venda, cfg.modo_teste
                        )

                        if resultado.get("sucesso"):
                            preco_exec = resultado.get("preco_execucao") or preco_atual
                            qty_exec = resultado.get("quantidade") or quantidade_venda
                            valor_realizado = qty_exec * preco_exec

                            # 🔥 subtrai apenas a quantidade realmente executada
                            trade.quantidade -= qty_exec

                            if trade.modo_teste:
                                self.wallet.update_test_balance(session, valor_realizado, "ADICIONAR")

                            self.notifier.send_message(
                                f"🔄 PARCIAL {trade.side}\n"
                                f"{trade.symbol}\n"
                                f"💰 Realizado: ${valor_realizado:.2f}\n"
                                f"📍 Preço Exec: {preco_exec:.4f}"
                            )

                            session.commit()

                        continue

                    # =========================
                    # 🏁 SAÍDA TOTAL
                    # =========================
                    if acao == "CLOSE_FULL":

                        if trade.quantidade <= 0:
                            continue

                        # 🔥 CORRIGIDO: converte quantidade para valor USDT
                        valor_total = trade.quantidade * preco_atual

                        resultado = await asyncio.to_thread(
                            self.be.place_order,
                            trade.symbol, exit_side, valor_total, cfg.modo_teste
                        )

                        if resultado.get("sucesso"):
                            preco_exec = resultado.get("preco_execucao") or preco_atual
                            qty_exec = resultado.get("quantidade") or trade.quantidade
                            valor_retorno = qty_exec * preco_exec

                            if trade.modo_teste:
                                self.wallet.update_test_balance(session, valor_retorno, "ADICIONAR")

                            if trade.side == "LONG":
                                lucro_bruto = ((preco_exec / trade.preco_entrada) - 1) * 100
                                pnl_bruto = (preco_exec - trade.preco_entrada) * qty_exec
                            else:
                                lucro_bruto = ((trade.preco_entrada / preco_exec) - 1) * 100
                                pnl_bruto = (trade.preco_entrada - preco_exec) * qty_exec

                            # 🔥 desconta taxa estimada (0.05% entrada + 0.05% saída)
                            taxa_estimada = valor_retorno * 0.001
                            pnl_usd = pnl_bruto - taxa_estimada
                            lucro = lucro_bruto - 0.10

                            emoji_result = "🟢" if pnl_usd > 0 else "🔴"

                            historico = HistoricoTrade(
                                symbol=trade.symbol,
                                quantidade=qty_exec,
                                preco_entrada=trade.preco_entrada,
                                preco_saida=preco_exec,
                                valor_pago=trade.valor_pago,
                                lucro_percentual=lucro,
                                valor_lucro_usd=pnl_usd,
                                motivo_saida="IA_EXIT",
                                modo_teste=trade.modo_teste,
                                data_saida=datetime.now()
                            )

                            self.notifier.send_message(
                                f"🏁 SAÍDA {trade.side}\n"
                                f"{trade.symbol}\n\n"
                                f"{emoji_result} Resultado: {lucro:.2f}%\n"
                                f"💵 PnL: ${pnl_usd:.2f}\n"
                                f"💰 Valor Final: ${valor_retorno:.2f}\n"
                                f"📍 Preço Entrada: {trade.preco_entrada:.4f}\n"
                                f"📍 Preço Saída: {preco_exec:.4f}"
                            )

                            session.add(historico)
                            session.delete(trade)
                            session.commit()

            except Exception as e:
                print(f"❌ [EXECUTOR ERROR] {e}")
                session.rollback()

            finally:
                session.close()

            await asyncio.sleep(5)