from datetime import datetime

import pytest

from agrosmart.etapas import LeituraInvalida, enriquecer, interpretar, para_numero, validar
from agrosmart.regras import analisar, classificar

AGORA = datetime(2026, 11, 21, 12, 0, 0)


def leitura_ok(**extra):
    base = {
        "dispositivo": "Sensor_001", "local": "Estufa A", "timestamp": AGORA,
        "temperatura": 30.0, "umidade_solo": 55.0, "luminosidade": 500.0,
    }
    base.update(extra)
    return base


@pytest.mark.parametrize(
    "texto, esperado",
    [("27,8 C", 27.8), ("54%", 54.0), (742, 742.0), ("-150 C", -150.0), (None, None), ("abc", None)],
)
def test_para_numero(texto, esperado):
    assert para_numero(texto) == esperado


@pytest.mark.parametrize(
    "valor, esperado",
    [(27.9, "ABAIXO"), (28, "OK"), (30, "OK"), (32, "OK"), (32.1, "ACIMA")],
)
def test_classificar_limites_da_faixa(valor, esperado):
    assert classificar(valor, (28, 32)) == esperado


def test_risco_normal():
    r = analisar(leitura_ok())
    assert r["nivel_risco"] == "NORMAL"
    assert r["qtd_problemas"] == 0
    assert r["recomendacao"] == "Nenhuma ação necessária."


def test_risco_atencao_com_um_problema():
    r = analisar(leitura_ok(temperatura=35.0))
    assert r["nivel_risco"] == "ATENÇÃO"
    assert r["status_temperatura"] == "ACIMA"
    assert "ventilação" in r["recomendacao"]


def test_risco_critico_com_dois_problemas():
    r = analisar(leitura_ok(temperatura=20.0, umidade_solo=30.0))
    assert r["nivel_risco"] == "CRÍTICO"
    assert r["qtd_problemas"] == 2
    assert "irrigação" in r["recomendacao"]


def test_luminosidade_nao_entra_na_decisao():
    assert analisar(leitura_ok(luminosidade=0.0))["nivel_risco"] == "NORMAL"


def test_interpretar_json_valido_com_unidades():
    linha = (
        '{"device": "S1", "location": "A", "date": "21/11/2026", "time": "10:30:00",'
        ' "temperature": "27,8 C", "reading": {"light": 742, "soil_moisture": "54%"}}'
    )
    leitura = interpretar(linha, AGORA)
    assert leitura["temperatura"] == 27.8
    assert leitura["umidade_solo"] == 54.0
    assert leitura["luminosidade"] == 742.0
    assert leitura["timestamp"] == datetime(2026, 11, 21, 10, 30, 0)


def test_interpretar_formato_tinkercad_usa_hora_da_ingestao():
    linha = '{"t_s": 10, "temperature": 29.5, "reading": {"light": 300, "soil_moisture": 50}}'
    assert interpretar(linha, AGORA)["timestamp"] == datetime(2026, 11, 21, 12, 0, 10)


@pytest.mark.parametrize("linha", ["{ quebrado", "[1, 2]", '{"date": "99/99/9999", "time": "10:00:00"}'])
def test_interpretar_rejeita_linhas_invalidas(linha):
    with pytest.raises(LeituraInvalida):
        interpretar(linha, AGORA)


def test_validar_aceita_leitura_boa():
    assert validar(leitura_ok()) is None


def test_validar_campo_nulo():
    assert validar(leitura_ok(umidade_solo=None)) == "campo nulo ou ausente"


def test_validar_fora_da_faixa_fisica():
    assert validar(leitura_ok(temperatura=-150.0)) == "valor fora da faixa (sensor suspeito)"
    assert validar(leitura_ok(umidade_solo=120.0)) == "valor fora da faixa (sensor suspeito)"


def test_enriquecer_calcula_campos_derivados():
    r = enriquecer(leitura_ok(luminosidade=1023.0))
    assert r == {"luz_pct": 100.0, "data": "2026-11-21", "hora": 12}
