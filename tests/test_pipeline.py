import json
from datetime import datetime

import pandas as pd

from agrosmart.healthcheck import verificar
from agrosmart.historico import gerar_historico
from agrosmart.pipeline import Pipeline
from agrosmart.simulador import Sensor, gerar_linhas


def linha(hora, temp, solo, luz=500, dispositivo="S1"):
    return json.dumps({
        "device": dispositivo, "location": "Estufa A", "date": "21/11/2026", "time": f"{hora}:00:00",
        "temperature": f"{temp} C".replace(".", ","),
        "reading": {"light": luz, "soil_moisture": f"{solo}%"},
    })


def preparar(tmp_path, linhas):
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    (raw / "teste.jsonl").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return Pipeline(tmp_path, intervalo_s=30)


def test_ciclo_processa_valida_e_classifica(tmp_path):
    p = preparar(tmp_path, [
        linha("08", 30, 55),                      # NORMAL
        linha("09", 35, 55),                      # ATENÇÃO
        linha("10", 20, 30),                      # CRÍTICO
        "{ json quebrado",                        # rejeitada
        linha("11", -150, 55),                    # rejeitada (fora da faixa)
        linha("08", 30, 55),                      # rejeitada (duplicada)
    ])
    resumo = p.executar_ciclo()

    assert resumo["lidos"] == 6
    assert resumo["aceitos"] == 3
    assert resumo["rejeitados"] == 3
    assert (resumo["atencao"], resumo["criticos"]) == (1, 1)

    analisadas = pd.read_csv(p.analisadas_csv, sep=";", decimal=",", encoding="utf-8-sig")
    assert list(analisadas["nivel_risco"]) == ["NORMAL", "ATENÇÃO", "CRÍTICO"]
    assert len(analisadas.columns) == 14
    assert len(pd.read_csv(p.alertas_csv, sep=";", decimal=",", encoding="utf-8-sig")) == 2
    motivos = set(pd.read_csv(p.rejeitados_csv)["motivo"])
    assert motivos == {"JSON inválido", "valor fora da faixa (sensor suspeito)", "registro duplicado"}


def test_status_e_execucoes_sao_gravados(tmp_path):
    p = preparar(tmp_path, [linha("08", 30, 55), linha("09", 35, 55), "{ quebrado"])
    p.executar_ciclo()
    status = json.loads(p.status_json.read_text(encoding="utf-8"))
    assert status["estado"] == "OK"
    assert status["total_lidos"] == 3
    assert status["total_rejeitados"] == 1
    assert status["taxa_rejeicao_pct"] == 33.3
    assert status["leituras_por_nivel"] == {"NORMAL": 1, "ATENÇÃO": 1, "CRÍTICO": 0}
    execucoes = pd.read_csv(p.execucoes_csv, sep=";", decimal=",", encoding="utf-8-sig")
    assert list(execucoes["ciclo"]) == [1]
    assert verificar("pipeline", tmp_path, 30)[0] is True


def test_segundo_ciclo_so_processa_linhas_novas(tmp_path):
    p = preparar(tmp_path, [linha("08", 30, 55)])
    p.executar_ciclo()
    segundo = p.executar_ciclo()
    assert segundo["lidos"] == 0

    with (tmp_path / "data" / "raw" / "teste.jsonl").open("a", encoding="utf-8") as f:
        f.write(linha("09", 31, 60) + "\n")
        f.write("linha incompleta sem quebra")  # ainda sendo escrita: deve esperar
    terceiro = p.executar_ciclo()
    assert terceiro["lidos"] == 1
    assert len(pd.read_csv(p.trusted_csv)) == 2
    execucoes = pd.read_csv(p.execucoes_csv, sep=";", decimal=",", encoding="utf-8-sig")
    assert list(execucoes["ciclo"]) == [1, 2, 3]


def test_event_log_registra_o_caminho_de_cada_leitura(tmp_path):
    p = preparar(tmp_path, [linha("08", 35, 55), "{ quebrado"])
    p.executar_ciclo()
    eventos = pd.read_csv(p.eventos_csv)
    assert list(eventos.columns) == ["case_id", "atividade", "timestamp", "recurso", "resultado"]
    caminho = eventos[eventos["case_id"] == "teste.jsonl#1"]["atividade"].tolist()
    assert caminho == [
        "Leitura coletada", "Leitura recebida", "Leitura validada", "Leitura transformada",
        "Risco classificado", "Alerta emitido", "Saídas publicadas",
    ]
    rejeitado = eventos[eventos["case_id"] == "teste.jsonl#2"]["atividade"].tolist()
    assert rejeitado == ["Leitura recebida", "Leitura rejeitada", "Rejeição registrada"]


def test_reset_apaga_saidas(tmp_path):
    p = preparar(tmp_path, [linha("08", 30, 55)])
    p.executar_ciclo()
    p.resetar()
    assert not p.trusted_csv.exists()
    assert not p.estado_json.exists()


def test_historico_da_fase6_reproduz_os_numeros(tmp_path):
    gerar_historico(tmp_path, inicio=datetime(2026, 11, 19))
    resumo = Pipeline(tmp_path).executar_ciclo()
    assert resumo["lidos"] == 98
    assert resumo["aceitos"] == 93
    assert resumo["rejeitados"] == 5


def test_healthcheck_falha_sem_status(tmp_path):
    assert verificar("pipeline", tmp_path, 30)[0] is False
    assert verificar("simulador", tmp_path, 2)[0] is False


def test_simulador_gera_linhas_validas_e_injeta_falhas():
    import random

    rng = random.Random(1)
    sensor = Sensor(rng)
    momento = datetime(2026, 11, 21, 12, 0, 0)
    normais = gerar_linhas(sensor, momento, rng, 0.0)
    assert len(normais) == 1
    assert json.loads(normais[0])["reading"]["soil_moisture"].endswith("%")

    com_falha = [gerar_linhas(sensor, momento, rng, 1.0) for _ in range(40)]
    assert any(len(x) == 2 for x in com_falha)  # duplicada
    quebradas = 0
    for x in com_falha:
        try:
            json.loads(x[0])
        except json.JSONDecodeError:
            quebradas += 1
    assert quebradas > 0  # JSON truncado
