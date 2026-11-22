"""Etapas da pipeline aplicadas, uma leitura por vez de RAW -> TRUSTED -> REFINED."""

import json
import math
from datetime import datetime, timedelta

from agrosmart.regras import FAIXAS_FISICAS

OBRIGATORIOS = ("dispositivo", "timestamp", "temperatura", "umidade_solo", "luminosidade")


class LeituraInvalida(Exception):
    """Linha que não pode ser interpretada."""
    def __init__(self, motivo):
        super().__init__(motivo)
        self.motivo = motivo


def para_numero(valor):
    """Converte '27,8 C', '54%' ou 742 em número. Devolve None se não der."""
    if valor is None:
        return None
    texto = str(valor).replace("%", "").replace("C", "").replace(",", ".").strip()
    try:
        return float(texto)
    except ValueError:
        return None


def interpretar(linha, agora):
    """Padroniza o JSON."""
    try:
        dado = json.loads(linha)
    except json.JSONDecodeError as erro:
        raise LeituraInvalida("JSON inválido") from erro
    if not isinstance(dado, dict):
        raise LeituraInvalida("JSON inválido")

    leitura = dado.get("reading")
    if not isinstance(leitura, dict):
        leitura = {}

    try:
        if dado.get("date") and dado.get("time"):
            momento = datetime.strptime(f"{dado['date']} {dado['time']}", "%d/%m/%Y %H:%M:%S")
        else:  #usa a hora da ingestão + t_s
            momento = agora + timedelta(seconds=float(dado.get("t_s", 0)))
    except (ValueError, TypeError) as erro:
        raise LeituraInvalida("data/hora inválida") from erro

    return {
        "dispositivo": dado.get("device"),
        "local": dado.get("location"),
        "timestamp": momento,
        "temperatura": para_numero(dado.get("temperature")),
        "umidade_solo": para_numero(leitura.get("soil_moisture")),
        "luminosidade": para_numero(leitura.get("light")),
    }


def validar(leitura):
    """Devolve o motivo da rejeição, ou None se a leitura é confiável."""
    for campo in OBRIGATORIOS:
        valor = leitura.get(campo)
        if valor is None or (isinstance(valor, float) and math.isnan(valor)):
            return "campo nulo ou ausente"
    for campo, (minimo, maximo) in FAIXAS_FISICAS.items():
        if not minimo <= leitura[campo] <= maximo:
            return "valor fora da faixa (sensor suspeito)"
    return None


def enriquecer(leitura):
    """Camada REFINED: campos derivados usados no dashboard."""
    momento = leitura["timestamp"]
    return {
        "luz_pct": round(leitura["luminosidade"] / 1023 * 100, 1),
        "data": momento.strftime("%Y-%m-%d"),
        "hora": momento.hour,
    }
