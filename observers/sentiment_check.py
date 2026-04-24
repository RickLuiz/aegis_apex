import httpx
import asyncio

class SentimentCheck:
    """O 'Termômetro' do Aegis Alpha: Mede o medo e a ganância do mercado global."""

    def __init__(self):
        self.api_url = "https://api.alternative.me/fng/"

    async def get_market_sentiment(self) -> dict:
        """
        Busca o Fear & Greed Index em tempo real.
        Retorno: 0-24 (Pânico), 25-44 (Medo), 45-55 (Neutro), 56-75 (Ganância), 76-100 (Euforia).
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.api_url, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    value = int(data['data'][0]['value'])
                    classification = data['data'][0]['value_classification']
                    
                    return {
                        "score": value,
                        "status": classification,
                        "risk_level": self._classify_risk(value)
                    }
                return {"score": 50, "status": "Neutral", "risk_level": "MEDIUM"}
        except Exception as e:
            print(f"⚠️ [SENTIMENT] Erro ao buscar sentimento: {e}")
            return {"score": 50, "status": "Neutral", "risk_level": "MEDIUM"}

    def _classify_risk(self, value: int) -> str:
        if value <= 20: return "EXTREME_FEAR"
        if value <= 40: return "FEAR"
        if value >= 80: return "EXTREME_GREED"
        return "NORMAL"

    def get_sentiment_weight(self, sentiment_data: dict) -> float:
        """
        Transforma o sentimento em um multiplicador para o Ensemble.
        Extremo Medo: Reduz o apetite (proteção).
        Extrema Ganância: Cuidado com correções (reduz a mão).
        """
        risk = sentiment_data['risk_level']
        
        weights = {
            "EXTREME_FEAR": 0.5,  # Reduz a mão pela metade (mercado perigoso)
            "FEAR": 0.8,          # Reduz levemente
            "NORMAL": 1.0,        # Mão padrão
            "EXTREME_GREED": 0.7  # Reduz a mão (risco de topo)
        }
        return weights.get(risk, 1.0)