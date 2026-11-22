"""Pipeline AgroSmart: lê o RAW, valida, transforma, aplica as regras e publica as saídas.

Cada ciclo processa apenas as linhas novas dos arquivos data/raw/*.jsonl e registra:
  logs/agrosmart.log -> log da aplicação
  logs/status.json -> estado da última execução (usado pelo healthcheck)
  logs/execucoes.csv -> uma linha por ciclo (indicadores para o Power BI)
  logs/event_log.csv -> log de eventos por leitura (mineração de processos)
  logs/rejeitados.csv -> leituras descartadas e o motivo
"""

import pandas as pd
import argparse
import json
import logging
import os
import signal
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from agrosmart.etapas import LeituraInvalida, enriquecer, interpretar, validar
from agrosmart.regras import analisar

log = logging.getLogger("agrosmart")

COLUNAS_TRUSTED = ["dispositivo", "local", "timestamp", "temperatura", "umidade_solo", "luminosidade"]
COLUNAS_EVENTOS = ["case_id", "atividade", "timestamp", "recurso", "resultado"]
COLUNAS_EXECUCOES = [
    "ciclo", "inicio", "duracao_ms", "lidos", "aceitos", "rejeitados",
    "atencao", "criticos", "latencia_media_s",
]
NIVEIS = ("NORMAL", "ATENÇÃO", "CRÍTICO")


def configurar_log(pasta_logs):
    """Log no console e em arquivo."""
    pasta_logs.mkdir(parents=True, exist_ok=True)
    formato = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
    log.setLevel(logging.INFO)
    if log.handlers:
        return
    arquivo = RotatingFileHandler(
        pasta_logs / "agrosmart.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    console = logging.StreamHandler()
    for destino in (arquivo, console):
        destino.setFormatter(formato)
        log.addHandler(destino)


def agora():
    return datetime.now().replace(microsecond=0)


def _iso(momento):
    return momento.isoformat(timespec="milliseconds")


class Pipeline:
    def __init__(self, base=".", intervalo_s=30):
        self.base = Path(base)
        self.intervalo_s = intervalo_s
        self.raw = self.base / "data" / "raw"
        self.trusted_csv = self.base / "data" / "trusted" / "leituras_trusted.csv"
        self.refinado_csv = self.base / "data" / "refined" / "leituras_refinadas.csv"
        self.analisadas_csv = self.base / "data" / "refined" / "leituras_analisadas.csv"
        self.alertas_csv = self.base / "data" / "refined" / "alertas.csv"
        self.logs = self.base / "logs"
        self.rejeitados_csv = self.logs / "rejeitados.csv"
        self.eventos_csv = self.logs / "event_log.csv"
        self.execucoes_csv = self.logs / "execucoes.csv"
        self.status_json = self.logs / "status.json"
        self.estado_json = self.logs / "estado_ingestao.json"
        self._parar = threading.Event()
        for pasta in (self.raw, self.trusted_csv.parent, self.refinado_csv.parent, self.logs):
            pasta.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ utilidades
    def resetar(self):
        """Apaga saídas e controle de ingestão para reprocessar o RAW do zeroz."""
        for arquivo in (
            self.trusted_csv, self.refinado_csv, self.analisadas_csv, self.alertas_csv,
            self.rejeitados_csv, self.eventos_csv, self.execucoes_csv,
            self.status_json, self.estado_json,
        ):
            arquivo.unlink(missing_ok=True)
        log.info("saídas e controle de ingestão apagados")

    def _ler_estado(self):
        if self.estado_json.exists():
            return json.loads(self.estado_json.read_text(encoding="utf-8"))
        return {}

    def _novas_linhas(self, estado):
        """Devolve as linhas completas ainda não processadas e o novo estado."""
        novas, novo_estado = [], dict(estado)
        for arquivo in sorted(self.raw.glob("*.jsonl")):
            anterior = estado.get(arquivo.name, {"offset": 0, "linhas": 0})
            with arquivo.open("rb") as f:
                f.seek(anterior["offset"])
                dados = f.read()
            fim = dados.rfind(b"\n") + 1  # ignora a última linha se ainda estiver incompleta
            if fim == 0:
                continue
            texto = dados[:fim].decode("utf-8", errors="replace")
            numero = anterior["linhas"]
            for linha in texto.split("\n")[:-1]:
                numero += 1
                if linha.strip():
                    novas.append((arquivo.name, numero, linha.strip()))
            novo_estado[arquivo.name] = {"offset": anterior["offset"] + fim, "linhas": numero}
        return novas, novo_estado

    def _carregar_trusted(self):
        if self.trusted_csv.exists():
            return pd.read_csv(self.trusted_csv, parse_dates=["timestamp"])
        return pd.DataFrame(columns=COLUNAS_TRUSTED)

    @staticmethod
    def _chave(dispositivo, momento):
        return (dispositivo, pd.Timestamp(momento).strftime("%Y-%m-%dT%H:%M:%S"))

    # ------------------------------------------------------------------ um ciclo
    def executar_ciclo(self):
        inicio = datetime.now()
        referencia = agora()
        numero = self._proximo_ciclo()
        estado = self._ler_estado()
        novas, novo_estado = self._novas_linhas(estado)

        existentes = self._carregar_trusted()
        pares = zip(existentes["dispositivo"], existentes["timestamp"], strict=True)
        vistos = {self._chave(d, t) for d, t in pares}

        aceitas, rejeitadas, eventos, latencias = [], [], [], []
        contagem = dict.fromkeys(NIVEIS, 0)

        for arquivo, linha, texto in novas:
            caso = f"{arquivo}#{linha}"
            recebida = datetime.now()
            try:
                leitura = interpretar(texto, referencia)
            except LeituraInvalida as erro:
                eventos.append((caso, "Leitura recebida", recebida, "ingestao", ""))
                self._rejeitar(caso, arquivo, linha, erro.motivo, eventos, rejeitadas)
                continue

            coletada = min(leitura["timestamp"], recebida)
            eventos.append((caso, "Leitura coletada", coletada, "sensor", leitura["dispositivo"] or ""))
            eventos.append((caso, "Leitura recebida", recebida, "ingestao", arquivo))
            latencias.append((recebida - coletada).total_seconds())

            motivo = validar(leitura)
            if motivo is None:
                chave = self._chave(leitura["dispositivo"], leitura["timestamp"])
                if chave in vistos:
                    motivo = "registro duplicado"
                vistos.add(chave)
            if motivo:
                self._rejeitar(caso, arquivo, linha, motivo, eventos, rejeitadas)
                continue

            eventos.append((caso, "Leitura validada", datetime.now(), "validacao", ""))
            refinada = {**leitura, **enriquecer(leitura)}
            eventos.append((caso, "Leitura transformada", datetime.now(), "transformacao", ""))
            analise = analisar(refinada)
            nivel = analise["nivel_risco"]
            contagem[nivel] += 1
            eventos.append((caso, "Risco classificado", datetime.now(), "regras", nivel))
            if nivel != "NORMAL":
                eventos.append((caso, "Alerta emitido", datetime.now(), "regras", nivel))
                (log.error if nivel == "CRÍTICO" else log.warning)(
                    "alerta %s dispositivo=%s temperatura=%s umidade_solo=%s acao=%s",
                    nivel, leitura["dispositivo"], leitura["temperatura"],
                    leitura["umidade_solo"], analise["recomendacao"],
                )
            aceitas.append({k: leitura[k] for k in COLUNAS_TRUSTED})

        self._publicar(existentes, aceitas, rejeitadas)

        publicada = datetime.now()
        casos_aceitos = {e[0] for e in eventos if e[1] == "Leitura validada"}
        casos_rejeitados = {e[0] for e in eventos if e[1] == "Leitura rejeitada"}
        for caso in casos_aceitos:
            eventos.append((caso, "Saídas publicadas", publicada, "publicacao", ""))
        for caso in casos_rejeitados:
            eventos.append((caso, "Rejeição registrada", publicada, "publicacao", ""))
        self._gravar_eventos(eventos)
        self.estado_json.write_text(json.dumps(novo_estado), encoding="utf-8")

        duracao_ms = round((datetime.now() - inicio).total_seconds() * 1000)
        resumo = {
            "ciclo": numero,
            "inicio": inicio.isoformat(timespec="seconds"),
            "duracao_ms": duracao_ms,
            "lidos": len(novas),
            "aceitos": len(aceitas),
            "rejeitados": len(rejeitadas),
            "atencao": contagem["ATENÇÃO"],
            "criticos": contagem["CRÍTICO"],
            "latencia_media_s": round(sum(latencias) / len(latencias), 2) if latencias else 0.0,
        }
        self._gravar_execucao(resumo)
        self._gravar_status("OK", resumo)
        log.info(
            "ciclo=%d lidos=%d aceitos=%d rejeitados=%d atencao=%d criticos=%d "
            "duracao_ms=%d latencia_media_s=%.2f",
            numero, resumo["lidos"], resumo["aceitos"], resumo["rejeitados"],
            resumo["atencao"], resumo["criticos"], duracao_ms, resumo["latencia_media_s"],
        )
        return resumo

    @staticmethod
    def _rejeitar(caso, arquivo, linha, motivo, eventos, rejeitadas):
        eventos.append((caso, "Leitura rejeitada", datetime.now(), "validacao", motivo))
        rejeitadas.append({"arquivo": arquivo, "linha": linha, "motivo": motivo})
        log.warning("leitura rejeitada caso=%s motivo=%s", caso, motivo)

    # ------------------------------------------------------------------ saídas
    def _publicar(self, existentes, aceitas, rejeitadas):
        novas = pd.DataFrame(aceitas, columns=COLUNAS_TRUSTED)
        trusted = (
            pd.concat([existentes[COLUNAS_TRUSTED], novas], ignore_index=True)
            .sort_values("timestamp", kind="stable")
            .reset_index(drop=True)
        )
        registros = trusted.to_dict("records")
        refinados = [{**r, **enriquecer(r)} for r in registros]
        analisados = [{**r, **analisar(r)} for r in refinados]
        colunas_refinado = COLUNAS_TRUSTED + ["luz_pct", "data", "hora"]
        refinado = pd.DataFrame(refinados, columns=colunas_refinado)
        colunas_analise = colunas_refinado + [
            "status_temperatura", "status_umidade_solo", "qtd_problemas",
            "nivel_risco", "recomendacao",
        ]
        analisadas = pd.DataFrame(analisados, columns=colunas_analise)
        alertas = analisadas[analisadas["nivel_risco"] != "NORMAL"]

        trusted.to_csv(self.trusted_csv, index=False)
        refinado.to_csv(self.refinado_csv, index=False)
        # sep=";" e decimal="," para o Power BI em português abrir sem erro
        formato_bi = {"index": False, "sep": ";", "decimal": ",", "encoding": "utf-8-sig"}
        analisadas.to_csv(self.analisadas_csv, **formato_bi)
        alertas.to_csv(self.alertas_csv, **formato_bi)

        if rejeitadas:
            antigas = pd.read_csv(self.rejeitados_csv) if self.rejeitados_csv.exists() else None
            todas = pd.concat([antigas, pd.DataFrame(rejeitadas)], ignore_index=True)
            todas.to_csv(self.rejeitados_csv, index=False)

    def _gravar_eventos(self, eventos):
        if not eventos:
            return
        tabela = pd.DataFrame(eventos, columns=COLUNAS_EVENTOS)
        tabela["timestamp"] = tabela["timestamp"].map(_iso)
        novo = not self.eventos_csv.exists()
        tabela.to_csv(self.eventos_csv, mode="a", header=novo, index=False)

    def _proximo_ciclo(self):
        if not self.execucoes_csv.exists():
            return 1
        with self.execucoes_csv.open(encoding="utf-8-sig") as f:
            return max(sum(1 for _ in f), 1)  # linhas = cabeçalho + ciclos anteriores

    def _gravar_execucao(self, resumo):
        linha = pd.DataFrame([resumo], columns=COLUNAS_EXECUCOES)
        novo = not self.execucoes_csv.exists()
        linha.to_csv(
            self.execucoes_csv, mode="a", header=novo, index=False,
            sep=";", decimal=",", encoding="utf-8-sig" if novo else "utf-8",
        )

    def _gravar_status(self, estado, resumo, erro=None):
        total_aceitos = len(self._carregar_trusted())
        total_rejeitados = len(pd.read_csv(self.rejeitados_csv)) if self.rejeitados_csv.exists() else 0
        total_lidos = total_aceitos + total_rejeitados
        niveis = dict.fromkeys(NIVEIS, 0)
        if self.analisadas_csv.exists():
            tabela = pd.read_csv(self.analisadas_csv, sep=";", decimal=",", encoding="utf-8-sig")
            niveis.update(tabela["nivel_risco"].value_counts().to_dict())
        status = {
            "estado": estado,
            "atualizado_em": agora().isoformat(),
            "intervalo_s": self.intervalo_s,
            "ultimo_ciclo": resumo,
            "total_lidos": total_lidos,
            "total_aceitos": total_aceitos,
            "total_rejeitados": total_rejeitados,
            "taxa_rejeicao_pct": round(100 * total_rejeitados / total_lidos, 1) if total_lidos else 0.0,
            "leituras_por_nivel": {k: int(v) for k, v in niveis.items()},
            "erro": erro,
        }
        self.status_json.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    # laço principal
    def executar(self, uma_vez=False):
        log.info("pipeline iniciado base=%s intervalo_s=%s", self.base.resolve(), self.intervalo_s)
        while not self._parar.is_set():
            try:
                self.executar_ciclo()
            except Exception as erro:  # o serviço não cai porconta de um ciclo ruim
                log.exception("falha no ciclo")
                self._gravar_status("ERRO", {}, erro=str(erro))
                if uma_vez:
                    raise
            if uma_vez:
                break
            self._parar.wait(self.intervalo_s)
        log.info("pipeline encerrado")

    def parar(self, *_):
        self._parar.set()


def main():
    parser = argparse.ArgumentParser(description="Pipeline AgroSmart")
    parser.add_argument("--base", default=os.environ.get("AGROSMART_BASE", "."))
    parser.add_argument("--intervalo", type=float, default=float(os.environ.get("CICLO_S", 30)))
    parser.add_argument("--once", action="store_true", help="executa um ciclo e termina")
    parser.add_argument("--reset", action="store_true", help="apaga saídas e reprocessa o RAW")
    args = parser.parse_args()

    configurar_log(Path(args.base) / "logs")
    pipeline = Pipeline(args.base, args.intervalo)
    signal.signal(signal.SIGTERM, pipeline.parar)
    signal.signal(signal.SIGINT, pipeline.parar)
    if args.reset:
        pipeline.resetar()
    pipeline.executar(uma_vez=args.once)


if __name__ == "__main__":
    main()
