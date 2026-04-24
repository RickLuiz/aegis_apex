import pandas as pd
from brain.feature_factory import FeatureFactory
from brain.ensemble import AegisEnsemble

class BacktestEngine:
    """O Simulador de Combate: Testa a estratégia em dados históricos."""

    def __init__(self, binance_engine, initial_capital=1000.0):
        self.be = binance_engine
        self.factory = FeatureFactory()
        self.ensemble = AegisEnsemble(self.be)
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.trades = []

    async def run_backtest(self, symbol: str, interval: str, days: int):
        print(f"🧪 [BACKTEST] Iniciando simulação para {symbol} ({days} dias)...")
        
        # 1. Coleta dados históricos (OHLCV)
        # Nota: Você precisará de um método no binance_engine que busque dados longos
        df = self.be.get_historical_klines(symbol, interval, days)
        if df.empty: return

        # 2. Calcula indicadores para todo o período
        df_features = self.factory.calculate_indicators(df)

        # 3. Simula o loop do mercado (Janela deslizante)
        for i in range(200, len(df_features)):
            window = df_features.iloc[i-1:i] # Simula o "agora"
            veredito = await self.ensemble.evaluate(symbol, window)

            if veredito['decisao'] != "SKIP":
                preco_entrada = df_features['close'].iloc[i]
                # Aqui você simularia a saída baseada no seu Trailing Stop
                # Para o backtest inicial, vamos usar um Take Profit fixo de 2%
                self._simulate_trade(symbol, preco_entrada, veredito)

        self._print_results()

    def _simulate_trade(self, symbol, preco, veredito):
        # Lógica simplificada de contabilização
        print(f"✅ Sinal de {veredito['decisao']} em {preco}")
        # Adiciona à lista de trades para cálculo de PnL final