"""Gera o histórico de 2 dias , 1 leitura a cada 30 minutos.
Contém falhas propositais."""

import argparse
import json
import math
import os
import random
from datetime import datetime, timedelta
from pathlib import Path


def gerar_historico(base=".", inicio=None):
    rng = random.Random(42)  # nosec B311
    if inicio is None:
        inicio = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)
    umidade = 60.0
    linhas = []

    for i in range(96):
        t = inicio + timedelta(minutes=30 * i)
        h = t.hour + t.minute / 60
        temp = 30 + 3 * math.sin((h - 9) / 24 * 2 * math.pi) + rng.uniform(-0.8, 0.8)
        umidade -= 0.35 + rng.uniform(0, 0.1)
        if umidade < 35:
            umidade += 25
        luz = 1000 * math.sin((h - 6) / 12 * math.pi) if 6 <= h <= 18 else 0
        luz = int(max(0, min(1023, luz + rng.uniform(-20, 20))))

        reg = {
            "device": "Sensor_001",
            "location": "Estufa A",
            "date": t.strftime("%d/%m/%Y"),
            "time": t.strftime("%H:%M:%S"),
            "temperature": f"{temp:.1f} C".replace(".", ","),
            "battery": "87%",
            "status": "online",
            "reading": {"light": luz, "soil_moisture": f"{umidade:.0f}%"},
            "firmware": "v1.4.2",
        }
        # erros propositais para testar a validação
        if i == 20:
            reg["temperature"] = "-150 C"
        if i == 35:
            del reg["reading"]["soil_moisture"]
        if i == 70:
            reg["reading"]["soil_moisture"] = None

        linhas.append(json.dumps(reg, ensure_ascii=False))
        if i == 50:
            linhas.append(json.dumps(reg, ensure_ascii=False))  # linha duplicada
        if i == 80:
            linhas.append("{ linha quebrada sem fechar")  # JSON inválido

    destino = Path(base) / "data" / "raw" / "historico_sensores.jsonl"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return destino, len(linhas)


def main():
    parser = argparse.ArgumentParser(description="Gera o histórico da Fase 6")
    parser.add_argument("--base", default=os.environ.get("AGROSMART_BASE", "."))
    args = parser.parse_args()
    destino, total = gerar_historico(args.base)
    print(f"{total} linhas geradas em {destino}")


if __name__ == "__main__":
    main()
