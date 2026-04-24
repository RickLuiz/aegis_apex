import os
import telebot
from telebot import types
from datetime import datetime
from database.models import SessionLocal, Config, TradeAtivo, HistoricoTrade
from engine.analytics import AegisAnalytics
from engine.binance_client import AegisBinanceClient

class AegisNotifier:
    """Classe responsável por ENVIAR notificações (Push do Bot para você)."""
    def __init__(self):
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.bot = telebot.TeleBot(self.token)

    def send_message(self, text):
        try:
            def escape(text):
                escape_chars = r'\_*[]()~`>#+-=|{}.!'
                text = str(text)
                for ch in escape_chars:
                    text = text.replace(ch, f"\\{ch}")
                return text

            self.bot.send_message(
                self.chat_id,
                escape(text),
                parse_mode='MarkdownV2'
            )
        except Exception as e:
            print(f"❌ Erro ao enviar Telegram: {e}")

    def notify_signal(self, symbol, confidence, price, decisao):
        try:
            def escape(text):
                escape_chars = r'\_*[]()~`>#+-=|{}.!'
                text = str(text)
                for ch in escape_chars:
                    text = text.replace(ch, f"\\{ch}")
                return text

            if decisao == "BUY":
                tipo = "LONG"
                gatilho = "+0.6%"
            else:
                tipo = "SHORT"
                gatilho = "-0.6%"

            msg = (
                f"🎯 *SINAL DETECTADO*\n\n"
                f"💎 *Ativo:* {escape(symbol)}\n"
                f"📊 *Tipo:* {tipo}\n"
                f"🧠 *Confiança:* {escape(f'{confidence:.1f}%')}\n"
                f"💵 *Preço:* {escape(f'${price:,.2f}')}\n"
                f"⏳ Gatilho: {escape(gatilho)}"
            )

            self.bot.send_message(
                self.chat_id,
                msg,
                parse_mode='MarkdownV2'
            )

        except Exception as e:
            print(f"❌ Erro notify_signal: {e}")


class AegisManager:
    """Classe responsável pelo MENU e COMANDOS (Interação sua com o Bot)."""
    def __init__(self):
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.bot = telebot.TeleBot(self.token)
        self.analytics = AegisAnalytics()
        self.be = AegisBinanceClient()
        self.setup_handlers()

    def setup_handlers(self):
        # --- MENU PRINCIPAL ---
        @self.bot.message_handler(commands=['start', 'menu'])
        def main_menu(message):
            markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True, one_time_keyboard=False)
            markup.add(
                types.KeyboardButton('📊 Ver Status'),
                types.KeyboardButton('💰 Saldo de Posições'),
                types.KeyboardButton('📜 Histórico'),
                types.KeyboardButton('📈 Gráficos'),
                types.KeyboardButton('📥 Importar Carteira'),
                types.KeyboardButton('⚙️ Configurações'),
                types.KeyboardButton('🚨 PÂNICO'),
                types.KeyboardButton('🔌 Ligar / Desligar')
            )
            self.bot.send_message(message.chat.id, "🛡️ *Painel Aegis Alpha Online*", 
                                 parse_mode='Markdown', reply_markup=markup)

        # --- STATUS ---
        @self.bot.message_handler(func=lambda m: m.text == '📊 Ver Status')
        def status_bot(message):
            session = SessionLocal()
            cfg = session.query(Config).first()
            status = "✅ LIGADO" if cfg.bot_ligado else "❌ DESLIGADO"
            modo = "🧪 SIMULAÇÃO" if cfg.modo_teste else "💰 REAL"
            saldo_info = f"\n💵 Saldo Simulado: `${cfg.saldo_ficticio:,.2f}`" if cfg.modo_teste else ""
            
            msg = (f"🛡️ *STATUS ATUAL*\n\n"
                   f"🤖 Bot: {status}\n"
                   f"⚙️ Modo: {modo}{saldo_info}\n"
                   f"📊 Vol. Mínimo 24h: `${cfg.volume_minimo_24h:,.0f}`\n"
                   f"📈 Stop Loss: `{cfg.stop_loss_percentual}%`\n"
                   f"🛡️ Trailing: `{cfg.trailing_stop_percentual}%`\n"
                   f"🚀 Ativação Trailing: `+{cfg.ativacao_trailing_percentual}%` de lucro")
            self.bot.send_message(message.chat.id, msg, parse_mode='Markdown')
            session.close()

        # --- SALDO DE POSIÇÕES ---
        @self.bot.message_handler(func=lambda m: m.text == '💰 Saldo de Posições')
        def current_positions(message):
            session = SessionLocal()
            cfg = session.query(Config).first()
            trades = session.query(TradeAtivo).filter(TradeAtivo.modo_teste == cfg.modo_teste).all()
            
            if not trades:
                self.bot.send_message(message.chat.id, f"📭 Nenhuma posição aberta no modo *{('SIMULADO' if cfg.modo_teste else 'REAL')}*.", parse_mode='Markdown')
                session.close()
                return

            msg = f"💰 *POSIÇÕES {('SIMULADAS' if cfg.modo_teste else 'REAIS')}:*\n\n"
            for t in trades:
                p_atual = self.be.get_current_price(t.symbol)
                if p_atual:
                    lucro = ((p_atual / t.preco_entrada) - 1) * 100
                    msg += f"🔹 *{t.symbol}*: `{lucro:+.2f}%` (${p_atual:,.2f})\n"
            
            self.bot.send_message(message.chat.id, msg, parse_mode='Markdown')
            session.close()

        # --- HISTÓRICO ---
        @self.bot.message_handler(func=lambda m: m.text == '📜 Histórico')
        def show_history(message):
            session = SessionLocal()
            cfg = session.query(Config).first()
            try:
                trades = session.query(HistoricoTrade).filter(
                    HistoricoTrade.modo_teste == cfg.modo_teste
                ).order_by(HistoricoTrade.data_saida.desc()).limit(10).all()
                
                if not trades:
                    self.bot.send_message(message.chat.id, f"📜 Histórico vazio no modo *{('SIMULADO' if cfg.modo_teste else 'REAL')}*.")
                    return

                msg = f"📜 *ÚLTIMOS 10 TRADES ({('SIMULADO' if cfg.modo_teste else 'REAL')}):*\n\n"
                for t in trades:
                    emoji = "✅" if t.lucro_percentual > 0 else "❌"
                    msg += (f"{emoji} *{t.symbol}* | `{t.lucro_percentual:+.2f}%`\n"
                            f"📅 {t.data_saida.strftime('%d/%m %H:%M')} | {t.motivo_saida}\n"
                            f"━━━━━━━━━━━━━━━\n")
                self.bot.send_message(message.chat.id, msg, parse_mode='Markdown')
            finally:
                session.close()

        # --- GRÁFICOS ---
        @self.bot.message_handler(func=lambda m: m.text == '📈 Gráficos')
        def menu_graphs(message):
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("📅 Diário", callback_data="g_1"),
                types.InlineKeyboardButton("📅 Semanal", callback_data="g_7"),
                types.InlineKeyboardButton("📅 Mensal", callback_data="g_30")
            )
            self.bot.send_message(message.chat.id, "Escolha o período do gráfico:", reply_markup=markup)

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('g_'))
        def send_graph(call):
            days = int(call.data.split('_')[1])
            session = SessionLocal()
            cfg = session.query(Config).first()
            modo_atual = cfg.modo_teste
            session.close()

            self.bot.answer_callback_query(call.id, "Gerando...")
            buf, summary = self.analytics.generate_dashboard(days, modo_teste=modo_atual)
            
            if buf:
                self.bot.send_photo(call.message.chat.id, buf, caption=summary, parse_mode='Markdown')
            else:
                self.bot.send_message(call.message.chat.id, summary)

        # --- IMPORTAR CARTEIRA ---
        @self.bot.message_handler(func=lambda m: m.text == '📥 Importar Carteira')
        def import_wallet(message):
            self.bot.send_message(message.chat.id, "🔍 Acessando Binance e sincronizando ativos...")
            session = SessionLocal()
            try:
                balances = self.be.get_all_balances()
                count = 0
                for asset, qty in balances.items():
                    symbol = f"{asset}USDT"
                    price = self.be.get_current_price(symbol)
                    if price and (qty * price) > 10:
                        # IMPORTANTE: Definir valor_pago na importação para o ROI funcionar
                        valor_estatistico = qty * price
                        novo_trade = TradeAtivo(
                            symbol=symbol,
                            quantidade=qty,
                            preco_entrada=price,
                            valor_pago=valor_estatistico, # Valor no momento da importação
                            modo_teste=False,
                            maior_preco_atingido=price,
                            data_entrada=datetime.now()
                        )
                        session.add(novo_trade)
                        count += 1
                session.commit()
                self.bot.send_message(message.chat.id, f"✅ Sincronização concluída! *{count}* ativos importados.", parse_mode='Markdown')
            except Exception as e:
                self.bot.send_message(message.chat.id, f"❌ Erro na importação: {str(e)}")
            finally:
                session.close()

        # --- CONFIGURAÇÕES ---
        @self.bot.message_handler(func=lambda m: m.text == '⚙️ Configurações')
        def config_menu(message):
            session = SessionLocal()
            cfg = session.query(Config).first()
            modo_texto = "REAL 💰" if not cfg.modo_teste else "SIMULADO 🧪"
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton(f"🔄 Alternar para Modo {('SIMULADO' if not cfg.modo_teste else 'REAL')}", callback_data="toggle_mode"),
                types.InlineKeyboardButton(f"💵 Ajustar Saldo Simulado (${cfg.saldo_ficticio})", callback_data="edit_saldo_ficticio"),
                types.InlineKeyboardButton(f"📊 Volume Mínimo 24h (${cfg.volume_minimo_24h:,.0f})", callback_data="edit_volume_minimo_24h"),
                types.InlineKeyboardButton(f"🛑 Stop Loss ({cfg.stop_loss_percentual}%)", callback_data="edit_stop_loss_percentual"),
                types.InlineKeyboardButton(f"🛡️ Trailing Stop ({cfg.trailing_stop_percentual}%)", callback_data="edit_trailing_stop_percentual"),
                types.InlineKeyboardButton(f"🚀 Ativação Trailing ({cfg.ativacao_trailing_percentual}%)", callback_data="edit_ativacao_trailing_percentual"),
                types.InlineKeyboardButton(f"👥 Limite Posições ({cfg.limite_posicoes})", callback_data="edit_limite_posicoes"),
                types.InlineKeyboardButton(f"📊 Alocação ({cfg.saldo_percentual}%)", callback_data="edit_saldo_percentual"),
                types.InlineKeyboardButton(f"🔎 Qtd Ativos Scan ({cfg.max_ativos_scan})",callback_data="edit_max_ativos_scan")
            )
            
            self.bot.send_message(message.chat.id, f"⚙️ *Ajuste de Parâmetros*\nModo Ativo: *{modo_texto}*", 
                                 parse_mode='Markdown', reply_markup=markup)
            session.close()

        @self.bot.callback_query_handler(func=lambda call: call.data == "toggle_mode")
        def handle_toggle_mode(call):
            session = SessionLocal()
            cfg = session.query(Config).first()
            cfg.modo_teste = not cfg.modo_teste
            session.commit()
            
            novo_modo = "🧪 SIMULADO" if cfg.modo_teste else "💰 REAL"
            self.bot.answer_callback_query(call.id, f"Modo alterado para {novo_modo}")
            self.bot.edit_message_text(f"✅ Modo de operação alterado para: *{novo_modo}*", 
                                     call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            session.close()

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('edit_'))
        def ask_new_value(call):
            param = call.data.replace('edit_', '')
            param_nome = param.replace('_', ' ').title()
            msg = self.bot.send_message(call.message.chat.id, f"📝 Digite o novo valor para *{param_nome}*:", parse_mode='Markdown')
            self.bot.register_next_step_handler(msg, self.save_config, param)

        # --- PÂNICO CORRIGIDO ---
        @self.bot.message_handler(func=lambda m: m.text == '🚨 PÂNICO')
        def panic_confirm(message):
            session = SessionLocal()
            cfg = session.query(Config).first()
            trades_count = session.query(TradeAtivo).filter(TradeAtivo.modo_teste == cfg.modo_teste).count()
            session.close()

            if trades_count == 0:
                self.bot.send_message(message.chat.id, f"🚫 Não há posições abertas no modo *{('SIMULADO' if cfg.modo_teste else 'REAL')}*.")
                return

            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("☢️ SIM, VENDER TUDO!", callback_data="panic_execute"),
                types.InlineKeyboardButton("❌ CANCELAR", callback_data="panic_cancel")
            )
            
            self.bot.send_message(message.chat.id, 
                                 f"⚠ *ATENÇÃO!* Você vai fechar *{trades_count}* posições {('SIMULADAS' if cfg.modo_teste else 'REAIS')} a mercado.\n\n"
                                 "Deseja continuar?", 
                                 parse_mode='Markdown', reply_markup=markup)

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('panic_'))
        def handle_panic_callback(call):
            session = SessionLocal()
            cfg = session.query(Config).first()
            
            if call.data == "panic_execute":
                self.bot.edit_message_text("☢️ *EXECUTANDO PÂNICO...*", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
                trades = session.query(TradeAtivo).filter(TradeAtivo.modo_teste == cfg.modo_teste).all()
                
                count_sucesso = 0
                for t in trades:
                    try:
                        # 1. Executa venda na Binance (ou simula)
                        venda_ok = self.be.place_order(t.symbol, 'SELL', t.quantidade, modo_teste=t.modo_teste)
                        
                        if venda_ok:
                            p_saida = self.be.get_current_price(t.symbol) or t.preco_entrada
                            lucro_perc = ((p_saida / t.preco_entrada) - 1) * 100
                            lucro_abs = (p_saida - t.preco_entrada) * t.quantidade

                            # 2. Se for modo teste, devolve o valor ao saldo fictício
                            if t.modo_teste:
                                cfg.saldo_ficticio += (t.quantidade * p_saida)

                            # 3. REGISTRA NO HISTÓRICO PARA O ANALYTICS
                            historico = HistoricoTrade(
                                symbol=t.symbol,
                                quantidade=t.quantidade,
                                preco_entrada=t.preco_entrada,
                                preco_saida=p_saida,
                                valor_pago=t.valor_pago, # Crucial para o ROI
                                lucro_percentual=lucro_perc,
                                valor_lucro_usd=lucro_abs,
                                motivo_saida="🚨 PÂNICO",
                                modo_teste=t.modo_teste,
                                data_saida=datetime.now()
                            )
                            session.add(historico)
                            session.delete(t)
                            count_sucesso += 1
                    except Exception as e:
                        print(f"Erro ao fechar {t.symbol} no pânico: {e}")
                
                cfg.bot_ligado = False # Desliga o bot por segurança após o pânico
                session.commit()
                self.bot.send_message(call.message.chat.id, f"✅ *PÂNICO CONCLUÍDO:*\nFechados: {count_sucesso} trades.\nBot: DESLIGADO 🛑")
            else:
                self.bot.edit_message_text("✅ Operação de pânico cancelada.", call.message.chat.id, call.message.message_id)
            session.close()

        # --- LIGAR / DESLIGAR ---
        @self.bot.message_handler(func=lambda m: m.text == '🔌 Ligar / Desligar')
        def toggle_bot(message):
            session = SessionLocal()
            cfg = session.query(Config).first()
            cfg.bot_ligado = not cfg.bot_ligado
            session.commit()
            estado = "LIGADO 🚀" if cfg.bot_ligado else "DESLIGADO 🛑"
            self.bot.send_message(message.chat.id, f"Bot alterado para: *{estado}*", parse_mode='Markdown')
            session.close()

    def save_config(self, message, param):
        try:
            novo_valor = float(message.text.replace(',', '.'))
            session = SessionLocal()
            cfg = session.query(Config).first()
            setattr(cfg, param, novo_valor)
            session.commit()
            param_nome = param.replace('_', ' ').title()
            self.bot.send_message(message.chat.id, f"✅ *{param_nome}* atualizado para `{novo_valor:,.2f}`!", parse_mode='Markdown')
            session.close()
        except Exception as e:
            self.bot.send_message(message.chat.id, f"❌ Erro ao salvar valor: {e}")

    def run(self):
        print("📱 Aegis Manager Online.")
        self.bot.infinity_polling()