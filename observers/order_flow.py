import pandas as pd

class OrderFlowMonitor:
    """O 'Segurança' do Aegis Alpha: Analisa a pressão real do Book de Ofertas."""

    def __init__(self, binance_engine):
        # binance_engine aqui é a instância de AegisBinanceClient
        self.be = binance_engine

    def check_pressure(self, symbol: str) -> dict:
        """
        Analisa o desequilíbrio entre ordens de compra e venda (Order Book Imbalance).
        """
        try:
            # CORREÇÃO: Usando 'rest_client' que é o nome definido no seu AegisBinanceClient
            if self.be is None or self.be.rest_client is None:
                raise Exception("Motor da Binance não inicializado corretamente.")

            # Pega o livro de ofertas (profundidade 20)
            depth = self.be.rest_client.get_order_book(symbol=symbol, limit=20)
            
            # Soma o volume das 10 melhores ofertas de compra (bids) e venda (asks)
            # quote[1] é a quantidade (quantity) no retorno da Binance
            bids = sum([float(quote[1]) for quote in depth['bids'][:10]])
            asks = sum([float(quote[1]) for quote in depth['asks'][:10]])
            
            # Calcula o Imbalance: (Compra - Venda) / (Compra + Venda)
            total_volume = bids + asks
            imbalance = (bids - asks) / total_volume if total_volume > 0 else 0
            
            return {
                "imbalance": round(imbalance, 4),
                "bids_volume": round(bids, 2),
                "asks_volume": round(asks, 2),
                "dominancia": "COMPRA" if imbalance > 0.2 else "VENDA" if imbalance < -0.2 else "NEUTRO"
            }
        except Exception as e:
            # Logamos o erro mas retornamos valores neutros para o bot não travar
            print(f"❌ Erro ao analisar fluxo de {symbol}: {e}")
            return {
                "imbalance": 0.0, 
                "bids_volume": 0.0, 
                "asks_volume": 0.0, 
                "dominancia": "ERRO"
            }

    def is_confirmed(self, symbol: str, nota_corte: float = 0.15) -> bool:
        """Gatilho final: Só retorna True se houver pressão compradora real."""
        stats = self.check_pressure(symbol)
        return stats['imbalance'] >= nota_corte