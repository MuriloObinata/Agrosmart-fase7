"""Healthcheck dos containers: sai com código 0 representando que está saudável ou 1 com problemas.
  python -m agrosmart.healthcheck pipeline -> status.json recente e estado OK
  python -m agrosmart.healthcheck simulador -> sensor_stream.jsonl recebendo dados"""

import json
import os
import sys
import time
from pathlib import Path


def idade_s(arquivo):
    return time.time() - arquivo.stat().st_mtime


def verificar(alvo, base, intervalo_s):
    base = Path(base)
    if alvo == "pipeline":
        status = base / "logs" / "status.json"
        if not status.exists():
            return False, "status.json ainda não existe"
        dados = json.loads(status.read_text(encoding="utf-8"))
        if dados.get("estado") != "OK":
            return False, f"estado={dados.get('estado')} erro={dados.get('erro')}"
        limite = 3 * intervalo_s + 10
        if idade_s(status) > limite:
            return False, f"status.json parado há mais de {limite:.0f}s"
        return True, "pipeline OK"

    stream = base / "data" / "raw" / "sensor_stream.jsonl"
    if not stream.exists():
        return False, "sensor_stream.jsonl ainda não existe"
    limite = 10 * intervalo_s + 10
    if idade_s(stream) > limite:
        return False, f"sem leituras novas há mais de {limite:.0f}s"
    return True, "simulador OK"


def main():
    alvo = sys.argv[1] if len(sys.argv) > 1 else "pipeline"
    base = os.environ.get("AGROSMART_BASE", ".")
    variavel = "CICLO_S" if alvo == "pipeline" else "SIM_INTERVALO"
    intervalo = float(os.environ.get(variavel, 30 if alvo == "pipeline" else 2))
    ok, mensagem = verificar(alvo, base, intervalo)
    print(mensagem)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
