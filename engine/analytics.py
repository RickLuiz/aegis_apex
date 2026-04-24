import matplotlib
matplotlib.use('Agg')  # 🔥 força backend sem interface gráfica
import matplotlib.pyplot as plt
import pandas as pd
import io
from database.models import SessionLocal, HistoricoTrade, Config
from datetime import datetime, timedelta

class AegisAnalytics:
    def generate_dashboard(self, days=7, modo_teste=True):
        session = SessionLocal()
        try:
            # 1. BUSCAR TRADES DO PERÍODO
            date_limit = datetime.now() - timedelta(days=days)
            trades = session.query(HistoricoTrade).filter(
                HistoricoTrade.data_saida >= date_limit,
                HistoricoTrade.modo_teste == modo_teste
            ).order_by(HistoricoTrade.data_saida.asc()).all()

            label_modo = "SIMULADO" if modo_teste else "REAL"

            if not trades:
                return None, f"🚫 Nenhum trade {label_modo} encontrado no histórico."

            # 2. PROCESSAR DADOS
            data = []
            total_empenhado_periodo = 0
            
            for t in trades:
                # Fallback: Se valor_pago for nulo (trades antigos), calcula manualmente
                empenhado = getattr(t, 'valor_pago', None)
                if empenhado is None or empenhado == 0:
                    empenhado = t.preco_entrada * t.quantidade if (t.preco_entrada and t.quantidade) else 0
                
                total_empenhado_periodo += empenhado
                
                # Cálculo do Lucro Líquido
                if t.preco_saida and t.preco_entrada and t.quantidade:
                    lucro = (t.preco_saida - t.preco_entrada) * t.quantidade
                else:
                    lucro = getattr(t, 'valor_lucro_usd', 0) or 0

                data.append({
                    'data': t.data_saida,
                    'lucro': lucro,
                    'investimento': empenhado
                })
            
            df = pd.DataFrame(data)
            df['data'] = pd.to_datetime(df['data'])
            lucro_total_periodo = df['lucro'].sum()

            # --- CÁLCULO DO ROI (Lucro / Empenhado) ---
            roi_real = (lucro_total_periodo / total_empenhado_periodo * 100) if total_empenhado_periodo > 0 else 0

            # --- EIXO X: PERIODOS (1 2 3...) ---
            if days <= 1:
                df['periodo'] = df['data'].dt.hour
                xlabel = "Horas"
            elif days <= 31:
                df['periodo'] = df['data'].dt.day
                xlabel = "Dias do Mês"
            else:
                meses_pt = {1:'Jan', 2:'Fev', 3:'Mar', 4:'Abr', 5:'Mai', 6:'Jun', 
                            7:'Jul', 8:'Ago', 9:'Set', 10:'Out', 11:'Nov', 12:'Dez'}
                df['periodo'] = df['data'].dt.month.map(meses_pt)
                xlabel = "Meses"

            # Evolução para o gráfico
            df_evolucao = df.groupby('periodo', sort=False)['lucro'].sum().reset_index()
            df_evolucao['cum_lucro'] = df_evolucao['lucro'].cumsum()

            # --- GERAÇÃO DO GRÁFICO ---
            plt.style.use('dark_background')
            fig, ax = plt.subplots(figsize=(10, 5))
            color_line = '#00ff88' if lucro_total_periodo >= 0 else '#ff4444'
            
            ax.plot(df_evolucao['periodo'].astype(str), df_evolucao['cum_lucro'], 
                    color=color_line, marker='o', linewidth=2, label='Lucro Acumulado')
            ax.fill_between(df_evolucao['periodo'].astype(str), df_evolucao['cum_lucro'], color=color_line, alpha=0.1)
            
            ax.set_title(f"Performance {label_modo} (ROI: {roi_real:.2f}%)", fontsize=14, pad=20)
            ax.set_ylabel("Lucro em Reais (R$)")
            ax.set_xlabel(xlabel)

            # --- CARD COM ROI E ASSERTIVIDADE ---
            wins = len(df[df['lucro'] > 0])
            assertividade = (wins / len(df)) * 100 if len(df) > 0 else 0
            
            card_txt = (f"📈 ROI: {roi_real:+.2f}%\n"
                        f"🎯 Assertividade: {assertividade:.1f}%\n"
                        f"💰 Lucro: R$ {lucro_total_periodo:+.2f}\n"
                        f"📥 Empenhado: R$ {total_empenhado_periodo:.2f}")
            
            ax.text(0.02, 0.95, card_txt, transform=ax.transAxes, verticalalignment='top',
                    fontsize=10, bbox=dict(boxstyle='round,pad=0.5', facecolor='black', alpha=0.8, edgecolor=color_line))

            plt.tight_layout()
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=120)
            buf.seek(0)
            plt.close()

            summary = (
                f"📊 *RESULTADO OPERACIONAL*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🔥 *ROI:* `{roi_real:+.2f}%` (Lucro/Empenhado)\n"
                f"🎯 *ASSERTIVIDADE:* `{assertividade:.1f}%`\n"
                f"💵 *LUCRO LÍQUIDO:* `R$ {lucro_total_periodo:+.2f}`\n"
                f"📥 *TOTAL EMPENHADO:* `R$ {total_empenhado_periodo:.2f}`"
            )

            return buf, summary

        except Exception as e:
            import traceback
            print(traceback.format_exc()) # Log detalhado para debug
            return None, f"❌ Erro no Analytics: {e}"
        finally:
            session.close()