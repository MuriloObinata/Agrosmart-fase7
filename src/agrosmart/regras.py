"""Regras AgroSmart.
Faixas ideais: Temperatura 28-32C  | umidade do solo 40-70%
Faixas físicas: Limites de validade do sensor.
Risco: 0 problemas = NORMAL | 1 = ATENÇÃO | 2 = CRÍTICO."""

REGRAS = {
    "temperatura": (28, 32),
    "umidade_solo": (40, 70),
}

FAIXAS_FISICAS = {
    "temperatura": (-10, 60),
    "umidade_solo": (0, 100),
    "luminosidade": (0, 1023),
}

ACOES = {
    ("temperatura", "ABAIXO"): "Temperatura baixa: verificar aquecimento e fechar ventilação da estufa.",
    ("temperatura", "ACIMA"): "Temperatura alta: acionar ventilação ou sombreamento.",
    ("umidade_solo", "ABAIXO"): "Solo seco: acionar irrigação.",
    ("umidade_solo", "ACIMA"): "Solo encharcado: suspender irrigação.",
}


def classificar(valor, faixa):
    """Compara um valor com a faixa ideal: ABAIXO, ACIMA ou OK (limites contam como OK)."""
    minimo, maximo = faixa
    if valor < minimo:
        return "ABAIXO"
    if valor > maximo:
        return "ACIMA"
    return "OK"



def analisar(leitura):
    """Aplica as regras a uma leitura e devolve status, nível de risco e recomendação."""
    resultado = {}
    problemas = []
    for variavel, faixa in REGRAS.items():
        status = classificar(leitura[variavel], faixa)
        resultado[f"status_{variavel}"] = status
        if status != "OK":
            problemas.append((variavel, status))

    if len(problemas) == 0:
        risco = "NORMAL"
    elif len(problemas) == 1:
        risco = "ATENÇÃO"
    else:
        risco = "CRÍTICO"

    resultado["qtd_problemas"] = len(problemas)
    resultado["nivel_risco"] = risco
    resultado["recomendacao"] = (
        " | ".join(ACOES[p] for p in problemas) or "Nenhuma ação necessária."
    )
    return resultado
