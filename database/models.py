import os
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, UniqueConstraint, func, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

Base = declarative_base()
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# =========================
# CONFIG
# =========================
class Config(Base):
    __tablename__ = "configuracoes"

    id = Column(Integer, primary_key=True)
    bot_ligado = Column(Boolean, default=True)
    modo_teste = Column(Boolean, default=True)

    protecao_btc = Column(Float, default=-2.0)
    stop_loss_percentual = Column(Float, default=1.5)
    trailing_stop_percentual = Column(Float, default=0.5)
    ativacao_trailing_percentual = Column(Float, default=0.8)

    volume_minimo_24h = Column(Float, default=5000000.0)
    saldo_percentual = Column(Float, default=10.0)
    saldo_ficticio = Column(Float, default=1000.0)

    limite_posicoes = Column(Integer, default=3)
    max_ativos_scan = Column(Integer, default=30)

    token_telegram = Column(String, nullable=True)
    chat_id_telegram = Column(String, nullable=True)


# =========================
# FEATURE STORE
# =========================
class HistoricoFeature(Base):
    __tablename__ = "historico_features"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    close = Column(Float)
    volume = Column(Float)
    features = Column(JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint('symbol', 'timestamp', name='_symbol_timestamp_uc'),
    )


# =========================
# LOG IA
# =========================
class AegisLog(Base):
    __tablename__ = "aegis_logs"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, server_default=func.now())
    symbol = Column(String(20))
    decisao = Column(String(50))
    probabilidade_ia = Column(Float)
    dados_tecnicos = Column(JSON)
    resultado_posterior = Column(Float, nullable=True)
    modo_teste = Column(Boolean, default=True)


# =========================
# 🔥 TRADE ATIVO (AJUSTADO)
# =========================
class TradeAtivo(Base):
    __tablename__ = "trades_ativos"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False)

    # 🔥 NOVO CAMPO (CRÍTICO PARA FUTURES)
    side = Column(String(10), nullable=False, default="LONG")  # LONG ou SHORT

    quantidade = Column(Float, nullable=False)
    preco_entrada = Column(Float, nullable=False)
    valor_pago = Column(Float, nullable=False, default=0.0)

    preco_atual = Column(Float)

    # 🔥 topo ou fundo (dependendo do lado)
    maior_preco_atingido = Column(Float, nullable=False)

    trailing_percentual = Column(Float, default=0.02)
    parcial_realizada = Column(Boolean, default=False)

    stop_loss = Column(Float)
    trailing_stop_ativado = Column(Boolean, default=False)

    data_entrada = Column(DateTime, default=func.now())
    modo_teste = Column(Boolean, default=True)


# =========================
# HISTÓRICO
# =========================
class HistoricoTrade(Base):
    __tablename__ = "historico_trades"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False)

    quantidade = Column(Float)
    valor_pago = Column(Float, nullable=True)

    preco_entrada = Column(Float)
    preco_saida = Column(Float)

    lucro_percentual = Column(Float)
    valor_lucro_usd = Column(Float)

    data_entrada = Column(DateTime)
    data_saida = Column(DateTime, default=func.now())

    motivo_saida = Column(String(50))
    modo_teste = Column(Boolean, default=True, nullable=False)


# =========================
# OBSERVAÇÃO IA
# =========================
class ObservacaoIA(Base):
    __tablename__ = "observacao_ia"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(20))

    preco_sinal = Column(Float)
    probabilidade = Column(Float)
    valor_planejado = Column(Float)

    decisao = Column(String, default="SKIP")
    preco_referencia = Column(Float)

    data_identificacao = Column(DateTime, default=func.now())
    modo_teste = Column(Boolean, default=True)


# =========================
# INIT
# =========================
def init_db():
    Base.metadata.create_all(bind=engine)
    print("🛡️ [AEGIS] Banco atualizado com suporte a saída inteligente.")


if __name__ == "__main__":
    init_db()