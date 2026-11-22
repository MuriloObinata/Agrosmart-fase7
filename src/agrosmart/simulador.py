"""Simulador de sensor: grava uma leitura JSON em data/raw/sensor_stream.jsonl.
Imita o sensor do TinkerCad com dados sobre temperatura, umidade do solo e luminosidade.
injeta falhas propositais como JSON quebrados, outliers, campo ausentes, nulo e duplicados para que a validação da pipeline tenha o que rejeitar."""

import argparse
import json
import math
import os
import random
import time
from datetime import datetime
from pathlib import Path

ARQUIVO = "sensor_stream.jsonl"


class Sensor:
    """Estado físico simulado: temperatura oscila em torno de 30 C; o solo seca e é irrigado."""

    def __init__(self, rng):
        self.rng = rng
        self.temperatura = 30.0
        self.umidade = rng.uniform(58, 68)

    def avancar(self, momento):
        self.temperatura += 0.15 * (30 - self.temperatura) + self.rng.gauss(0, 1.05)
        self.umidade -= 0.25 + self.rng.uniform(0, 0.1)
        if self.umidade < 35:  # irrigação "reseta" a umidade
            self.umidade += 30
        hora = momento.hour + momento.minute / 60
        luz = 1000 * math.sin((hora - 6) / 12 * math.pi) if 6 <= hora <= 18 else 0
        luz = int(max(0, min(1023, luz + self.rng.uniform(-20, 20))))
        return {
            "device": "Sensor_001",
            "location": "Estufa A",
            "date": momento.strftime("%d/%m/%Y"),
            "time": momento.strftime("%H:%M:%S"),
            "temperature": f"{self.temperatura:.1f} C".replace(".", ","),
            "reading": {"light": luz, "soil_moisture": f"{self.umidade:.0f}%"},
        }


def gerar_linhas(sensor, momento, rng, taxa_falhas):
    """Devolve as linhas a gravar nesta rodada, normalmente 1, às vezes com falha."""
    leitura = sensor.avancar(momento)
    if rng.random() >= taxa_falhas:
        return [json.dumps(leitura, ensure_ascii=False)]

    falha = rng.choice(["json", "outlier", "ausente", "nulo", "duplicada"])
    if falha == "json":
        texto = json.dumps(leitura, ensure_ascii=False)
        return [texto[: len(texto) // 2]]
    if falha == "outlier":
        leitura["temperature"] = "-150 C"
    elif falha == "ausente":
        del leitura["reading"]["soil_moisture"]
    elif falha == "nulo":
        leitura["reading"]["soil_moisture"] = None
    texto = json.dumps(leitura, ensure_ascii=False)
    return [texto, texto] if falha == "duplicada" else [texto]


def main():
    parser = argparse.ArgumentParser(description="Simulador de sensor AgroSmart")
    parser.add_argument("--base", default=os.environ.get("AGROSMART_BASE", "."))
    parser.add_argument("--intervalo", type=float, default=float(os.environ.get("SIM_INTERVALO", 2)))
    parser.add_argument("--falhas", type=float, default=float(os.environ.get("SIM_FALHAS", 0.05)))
    parser.add_argument("--n", type=int, default=0, help="quantidade de leituras (0 = infinito)")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    destino = Path(args.base) / "data" / "raw" / ARQUIVO
    destino.parent.mkdir(parents=True, exist_ok=True)
    # Simula ruído de sensor
    rng = random.Random(args.seed)  # nosec B311
    sensor = Sensor(rng)

    enviadas = 0
    print(f"simulador iniciado: intervalo={args.intervalo}s destino={destino}", flush=True)
    while args.n == 0 or enviadas < args.n:
        for linha in gerar_linhas(sensor, datetime.now(), rng, args.falhas):
            with destino.open("a", encoding="utf-8") as f:
                f.write(linha + "\n")
        enviadas += 1
        if enviadas % 15 == 0:
            print(f"simulador: {enviadas} leituras enviadas", flush=True)
        time.sleep(args.intervalo)


if __name__ == "__main__":
    main()
