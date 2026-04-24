import asyncio
import os
import warnings
import threading
from datetime import datetime
from dotenv import load_dotenv

from observers.market_scanner import AegisScanner
from engine.executor import AegisExecutor
from engine.notifier import AegisNotifier, AegisManager
from database.models import SessionLocal, Config, init_db
from engine.binance_client import AegisBinanceClient

warnings.filterwarnings("ignore", category=UserWarning)
load_dotenv()


async def bootstrap():
    """Inicializa o banco de dados e as configurações padrão."""
    init_db()
    session = SessionLocal()

    try:
        cfg = session.query(Config).first()

        if not cfg:
            novo_cfg = Config(
                bot_ligado=True,
                modo_teste=True,
                saldo_ficticio=1000.0,
                volume_minimo_24h=5000000.0,
                limite_posicoes=3,
                protecao_btc=-2.0,
                stop_loss_percentual=1.5,
                trailing_stop_percentual=0.5,
                ativacao_trailing_percentual=0.8,
                saldo_percentual=10.0
            )

            session.add(novo_cfg)
            session.commit()

            print("✅ Configurações iniciais criadas com suporte a Modo Simulado.")

        else:
            updated = False

            if cfg.saldo_ficticio is None:
                cfg.saldo_ficticio = 1000.0
                updated = True

            if updated:
                session.commit()
                print("🔧 Banco de dados atualizado com campos de saldo padrão.")

    except Exception as e:
        print(f"⚠️ Erro no bootstrap: {e}")

    finally:
        session.close()


async def main():
    await bootstrap()

    # =========================
    # 🔥 CLIENT ÚNICO (FUTURES SAFE)
    # =========================
    client = AegisBinanceClient()

    # =========================
    # COMPONENTES
    # =========================
    notifier = AegisNotifier()
    manager = AegisManager()

    # 🔥 injeta o mesmo client
    scanner = AegisScanner(client)
    executor = AegisExecutor(client)

    # =========================
    # TELEGRAM THREAD
    # =========================
    print("📱 [AEGIS] Ativando Menu Interativo...")
    threading.Thread(target=manager.run, daemon=True).start()

    # =========================
    # MODO ATUAL
    # =========================
    session = SessionLocal()

    try:
        cfg = session.query(Config).first()

        if cfg:
            modo_label = "🧪 SIMULADO" if cfg.modo_teste else "💰 REAL"
        else:
            modo_label = "⚠️ INDEFINIDO"

    finally:
        session.close()

    # =========================
    # NOTIFICAÇÃO INICIAL
    # =========================
    notifier.send_message(
        f"🛡️ *AEGIS ALPHA V1.0*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🚀 *Sistema Online*\n"
        f"⚙️ Modo Ativo: {modo_label}\n"
        f"📡 Scanner: Ativo\n"
        f"🎯 Executor: Ativo\n"
        f"📱 Menu: Ativo\n"
        f"━━━━━━━━━━━━━━━"
    )

    print(f"📡 [AEGIS] Iniciando motores de trading em modo {modo_label}...")

    # =========================
    # LOOP PRINCIPAL
    # =========================
    try:
        await asyncio.gather(
            scanner.start_monitoring(),
            executor.run_execution_loop()
        )

    except KeyboardInterrupt:
        print("\n🛑 [AEGIS] Desligamento solicitado pelo usuário.")
        notifier.send_message("⚠️ *Aegis Alpha:* Sistema desligado manualmente.")

    except Exception as e:
        print(f"🚨 [ERRO CRÍTICO]: {e}")
        notifier.send_message(f"🚨 *ERRO CRÍTICO NO SISTEMA:*\n`{e}`")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Erro ao iniciar loop principal: {e}")