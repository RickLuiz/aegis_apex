import os
from sqlalchemy.orm import Session
from database.models import Config, TradeAtivo, HistoricoTrade
from datetime import datetime


class WalletManager:
    """
    Gestão de capital + lógica de saída inteligente (IA + trailing)
    """

    def __init__(self, binance_client=None, ensemble=None):
        self.client = binance_client
        self.ensemble = ensemble

    # =========================
    # SALDO
    # =========================
    def get_available_balance(self, db: Session, modo_teste: bool = True) -> float:
        cfg = db.query(Config).first()

        if modo_teste:
            return float(cfg.saldo_ficticio) if cfg.saldo_ficticio else 0.0

        try:
            balances = self.client.get_all_balances()
            saldo = balances.get("USDT", 0.0)
            return float(saldo)

        except Exception as e:
            print(f"❌ Erro ao buscar saldo real: {e}")
            return 0.0

    # =========================
    # ABERTURA
    # =========================
    def can_open_position(self, db: Session, modo_teste: bool = True) -> bool:
        cfg = db.query(Config).first()

        posicoes_abertas = db.query(TradeAtivo).filter_by(modo_teste=modo_teste).count()
        if posicoes_abertas >= cfg.limite_posicoes:
            return False

        saldo_atual = self.get_available_balance(db, modo_teste)
        if saldo_atual < 10.0:
            return False

        return True

    # =========================
    # STOP DIÁRIO
    # =========================
    def check_daily_stop(self, db: Session, modo_teste: bool = True) -> bool:
        try:
            hoje = datetime.now().date()

            trades_hoje = db.query(HistoricoTrade).filter(
                HistoricoTrade.data_saida >= hoje,
                HistoricoTrade.modo_teste == modo_teste
            ).all()

            if not trades_hoje:
                return True

            pnl_dia = sum([t.lucro_percentual for t in trades_hoje if t.lucro_percentual])

            return pnl_dia > -3.0

        except Exception as e:
            print(f"⚠️ Erro no daily stop: {e}")
            return True

    # =========================
    # POSITION SIZE
    # =========================
    def calculate_position_size(self, db: Session, multiplicador: float = 1.0, modo_teste: bool = True) -> float:
        cfg = db.query(Config).first()
        saldo_disponivel = self.get_available_balance(db, modo_teste=modo_teste)

        if saldo_disponivel <= 0:
            return 0.0

        percentual = cfg.saldo_percentual if cfg.saldo_percentual else 10.0
        tamanho_base = saldo_disponivel * (percentual / 100)

        valor_final = tamanho_base * multiplicador

        # 🔥 proteção de saldo
        if valor_final > saldo_disponivel:
            valor_final = saldo_disponivel * 0.95

        # 🔥 mínimo absoluto
        MIN_NOTIONAL = 10

        if valor_final < MIN_NOTIONAL:
            return 0.0

        return float(valor_final)

    # =========================
    # SAÍDA INTELIGENTE 🔥
    # =========================
    async def manage_exit(self, trade: TradeAtivo, df_features, preco_atual: float, db: Session = None):
        """
        Decide: HOLD / CLOSE_PARTIAL / CLOSE_FULL
        combinando trailing + IA
        """

        cfg = db.query(Config).first() if db else None

        # =========================
        # ATUALIZA TOPO / FUNDO
        # =========================
        if trade.side == "LONG":
            if preco_atual > trade.maior_preco_atingido:
                trade.maior_preco_atingido = preco_atual
                if db:
                    db.commit()
        else:  # SHORT
            if preco_atual < trade.maior_preco_atingido:
                trade.maior_preco_atingido = preco_atual
                if db:
                    db.commit()

        # =========================
        # LUCRO
        # =========================
        if trade.side == "LONG":
            lucro = (preco_atual / trade.preco_entrada) - 1
        else:
            lucro = (trade.preco_entrada / preco_atual) - 1

        # =========================
        # DRAWDOWN (TRAILING)
        # =========================
        if trade.side == "LONG":
            drawdown = (preco_atual / trade.maior_preco_atingido) - 1
        else:
            drawdown = (trade.maior_preco_atingido / preco_atual) - 1

        ativacao_trailing = (cfg.ativacao_trailing_percentual / 100) if cfg else 0.008
        trailing_percent = (cfg.trailing_stop_percentual / 100) if cfg else 0.005

        # =========================
        # 1. STOP LOSS
        # =========================
        stop_loss = (cfg.stop_loss_percentual / 100) if cfg else 0.015

        if lucro <= -stop_loss:
            return "CLOSE_FULL"

        # =========================
        # 2. ATIVA TRAILING
        # =========================
        if not trade.trailing_stop_ativado and lucro >= ativacao_trailing:
            trade.trailing_stop_ativado = True
            print(f"🚀 Trailing ativado: {trade.symbol} ({trade.side})")

            if db:
                db.commit()

        # =========================
        # 3. TRAILING EM AÇÃO
        # =========================
        if trade.trailing_stop_ativado:

            if drawdown <= -trailing_percent:

                if self.ensemble:
                    try:
                        # 🔥 CORRIGIDO: await direto, sem loop.run_until_complete()
                        result = await self.ensemble.evaluate(trade.symbol, df_features.tail(1))

                        prob = result.get("confianca_ia", 0) / 100

                        if prob > 0.55:
                            return "HOLD"

                        return "CLOSE_FULL"

                    except Exception as e:
                        print(f"Erro IA saída: {e}")
                        return "CLOSE_FULL"

                return "CLOSE_FULL"

        # =========================
        # 4. PARCIAL
        # =========================
        if lucro >= 0.02 and not getattr(trade, "parcial_realizada", False):
            trade.parcial_realizada = True

            if db:
                db.commit()

            return "CLOSE_PARTIAL"

        return "HOLD"

    # =========================
    # UPDATE SALDO (TESTE)
    # =========================
    def update_test_balance(self, db: Session, valor: float, operacao: str = "SUBTRAIR"):
        cfg = db.query(Config).first()
        if not cfg:
            return

        valor_antigo = cfg.saldo_ficticio

        if operacao == "SUBTRAIR":
            novo_saldo = cfg.saldo_ficticio - valor
            cfg.saldo_ficticio = max(0, novo_saldo)
        else:
            cfg.saldo_ficticio += valor

        db.commit()

        print(f"💰 [WALLET] ${valor_antigo:.2f} ➡️ ${cfg.saldo_ficticio:.2f} ({operacao})")