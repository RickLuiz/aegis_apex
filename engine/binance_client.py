import os
import math
import time
import pandas as pd
from binance.client import Client
from binance import AsyncClient, BinanceSocketManager


class AegisBinanceClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")

        # Cliente REST (FUTURES TESTNET)
        self.rest_client = Client(
            self.api_key,
            self.api_secret,
            testnet=True
        )

        # 🔥 garantir endpoints corretos
        self.rest_client.API_URL = 'https://testnet.binancefuture.com'
        self.rest_client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'

        # 🔥 sync de tempo
        self.rest_client.timestamp_offset = 0
        self._sync_time()

        self.client = None
        self.bsm = None

        self.symbol_info_cache = {}

        self.last_symbol_update = 0

        self.valid_symbols = set()
        self._refresh_valid_symbols()

    # =========================
    # ⏱️ SYNC TEMPO
    # =========================
    def _sync_time(self):
        try:
            server_time = self.rest_client.futures_time()['serverTime']
            local_time = int(time.time() * 1000)
            self.rest_client.timestamp_offset = server_time - local_time
        except Exception as e:
            print(f"⚠️ Erro ao sincronizar tempo: {e}")

    # =========================
    # 🔥 VALIDAÇÃO SÍMBOLOS
    # =========================
    def _refresh_valid_symbols(self):
        try:
            info = self.rest_client.futures_exchange_info()

            valid = {
                s['symbol']
                for s in info['symbols']
                if s['status'] == 'TRADING'
                and s['contractType'] == 'PERPETUAL'
            }

            tickers = self.rest_client.futures_ticker()
            available = {t['symbol'] for t in tickers}

            self.valid_symbols = valid.intersection(available)

            self.last_symbol_update = time.time()

            print(f"✅ {len(self.valid_symbols)} símbolos válidos (TESTNET)")

        except Exception as e:
            print(f"❌ Erro ao atualizar símbolos: {e}")
            self.valid_symbols = set()

    def _ensure_symbols_updated(self):
        if time.time() - self.last_symbol_update > 1800:
            print("🔄 Atualizando lista de símbolos...")
            self._refresh_valid_symbols()

    # =========================
    # 🔧 PRECISÃO
    # =========================
    def get_symbol_rules(self, symbol):
        if symbol in self.symbol_info_cache:
            return self.symbol_info_cache[symbol]

        try:
            exchange_info = self.rest_client.futures_exchange_info()
            symbol_info = next(s for s in exchange_info['symbols'] if s['symbol'] == symbol)

            lot_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
            step_size = float(lot_filter['stepSize'])
            min_qty = float(lot_filter['minQty'])

            price_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER')
            tick_size = float(price_filter['tickSize'])

            notional_filter = next(
                (f for f in symbol_info['filters'] if f['filterType'] in ['MIN_NOTIONAL', 'NOTIONAL']),
                None
            )

            min_notional = float(notional_filter['notional']) if notional_filter else 5.0

            precision_qty = int(round(-math.log(step_size, 10), 0))
            precision_price = int(round(-math.log(tick_size, 10), 0))

            rules = {
                "precision_qty": precision_qty,
                "precision_price": precision_price,
                "step_size": step_size,
                "min_qty": min_qty,
                "min_notional": min_notional
            }

            self.symbol_info_cache[symbol] = rules
            return rules

        except Exception as e:
            print(f"⚠️ Erro ao obter regras de {symbol}: {e}")
            return {
                "precision_qty": 8,
                "precision_price": 8,
                "step_size": 0.0001,
                "min_qty": 0.0001,
                "min_notional": 5.0
            }

    def adjust_quantity(self, symbol, quantity):
        rules = self.get_symbol_rules(symbol)

        precision = rules["precision_qty"]
        step_size = rules["step_size"]
        min_qty = rules["min_qty"]

        factor = 10 ** precision
        qty = math.floor(quantity * factor) / factor

        if qty < min_qty:
            return 0

        return qty

    # =========================
    # 📊 MERCADO
    # =========================
    def get_current_price(self, symbol):
        try:
            ticker = self.rest_client.futures_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            print(f"❌ Erro ao buscar preço de {symbol}: {e}")
            return None

    def get_price(self, symbol):
        return self.get_current_price(symbol)

    def get_all_balances(self):
        try:
            self._sync_time()

            account = self.rest_client.futures_account_balance()

            #print(f"🔍 [DEBUG SALDO RAW] {account[:3]}") 

            balances = {}
            for item in account:
                available = float(item.get('availableBalance', 0))
                if available > 0:
                    balances[item['asset']] = available

            if balances:
                print(f"💼 [SALDO] Disponível: { {k: f'{v:.2f}' for k, v in balances.items()} }")

            return balances

        except Exception as e:
            print(f"❌ Erro ao buscar saldos: {e}")
            return {}

    def get_market_data(self, volume_min, btc_protecao_limite, blacklist=None, limit=30):
        try:
            self._ensure_symbols_updated()

            if blacklist is None:
                blacklist = set()

            ignore_always = {
                'USDTUSDT', 'FDUSDUSDT', 'TUSDUSDT',
                'BNBUSDT', 'EURUSDT', 'GBPUSDT'
            }

            tickers = self.rest_client.futures_ticker()

            moedas_validas = []
            for t in tickers:
                symbol = t['symbol']

                try:
                    volume = float(t['quoteVolume'])
                except:
                    continue

                if (
                    volume > volume_min
                    and symbol.endswith('USDT')
                    and symbol not in ignore_always
                    and symbol in self.valid_symbols
                ):
                    moedas_validas.append(t)

            moedas_validas.sort(key=lambda x: float(x['quoteVolume']), reverse=True)

            top_moedas = []
            for t in moedas_validas:
                symbol = t['symbol']

                if symbol in blacklist:
                    continue

                top_moedas.append(symbol)

                if len(top_moedas) >= limit:
                    break

            btc_ticker = self.rest_client.futures_ticker(symbol="BTCUSDT")
            btc_var = float(btc_ticker['priceChangePercent'])

            return top_moedas, btc_var

        except Exception as e:
            print(f"❌ Erro no Market Data: {e}")
            return [], 0.0

    # =========================
    # 📈 CANDLES
    # =========================
    def get_candles(self, symbol, interval='15m', limit=500, **kwargs):
        try:
            if symbol not in self.valid_symbols:
                return pd.DataFrame()

            try:
                klines = self.rest_client.futures_klines(
                    symbol=symbol,
                    interval=interval,
                    limit=limit,
                    **kwargs
                )

            except Exception as api_error:
                if "Invalid symbol" in str(api_error):
                    print(f"🚫 Removendo símbolo inválido: {symbol}")
                    self.valid_symbols.discard(symbol)
                    return pd.DataFrame()

                print(f"⚠️ Erro API candles {symbol}: {api_error}")
                return pd.DataFrame()

            if not klines:
                return pd.DataFrame()

            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ])

            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            df.dropna(subset=['open', 'high', 'low', 'close', 'volume'], inplace=True)

            return df if not df.empty else pd.DataFrame()

        except Exception as e:
            print(f"❌ Erro crítico candles {symbol}: {e}")
            return pd.DataFrame()

    # =========================
    # 💰 ORDENS
    # =========================
    def place_order(self, symbol, side, amount, modo_teste=True):

        if modo_teste:
            print(f"🧪 [SIMULADO] {side} {symbol} | Valor: {amount}")
            preco_sim = self.get_current_price(symbol) or 0.0
            return {"sucesso": True, "preco_execucao": preco_sim, "quantidade": amount}

        try:
            self._sync_time()

            price = self.get_current_price(symbol)
            if not price:
                return {"sucesso": False}

            rules = self.get_symbol_rules(symbol)
            min_qty = rules["min_qty"]
            min_notional = rules["min_notional"]

            raw_qty = amount / price
            quantity = self.adjust_quantity(symbol, raw_qty)

            if quantity < min_qty:
                print(f"🚫 Qty abaixo do mínimo ({symbol}) | {quantity} < {min_qty}")
                return {"sucesso": False}

            if quantity <= 0:
                print(f"🚫 Quantidade inválida ({symbol}) | qty ajustada=0")
                return {"sucesso": False}

            notional = quantity * price
            if notional < min_notional:
                print(f"🚫 Notional baixo ({symbol}) | {notional:.2f} < {min_notional}")
                return {"sucesso": False}

            order = self.rest_client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity
            )

            order_id = order.get("orderId")
            print(f"💰 Ordem enviada: {order_id}")

            # 🔥 Testnet não retorna execução imediata — busca ordem preenchida
            preco_execucao = price  # fallback
            qty_executada = quantity  # fallback

            for _ in range(5):
                time.sleep(0.5)
                try:
                    filled = self.rest_client.futures_get_order(symbol=symbol, orderId=order_id)
                    status = filled.get("status")
                    avg = float(filled.get("avgPrice", 0))
                    exec_qty = float(filled.get("executedQty", 0))

                    if status == "FILLED" and avg > 0:
                        preco_execucao = avg
                        qty_executada = exec_qty
                        break

                except Exception as e:
                    print(f"⚠️ Erro ao consultar ordem {order_id}: {e}")
                    break

            print(f"✅ Execução confirmada | Preço: {preco_execucao} | Qty: {qty_executada}")
            return {"sucesso": True, "preco_execucao": preco_execucao, "quantidade": qty_executada}

        except Exception as e:
            msg = str(e)

            if "-1021" in msg:
                print("⏱️ Re-sincronizando tempo...")
                self._sync_time()
                return {"sucesso": False}

            print(f"🚨 ERRO ORDEM ({symbol}): {e}")
            return {"sucesso": False}

    # =========================
    # ⚡ ASYNC
    # =========================
    async def init_session(self):
        self.client = await AsyncClient.create(
            self.api_key,
            self.api_secret,
            testnet=True
        )

        self.client.API_URL = 'https://testnet.binancefuture.com'
        self.client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'

        self.bsm = BinanceSocketManager(self.client)