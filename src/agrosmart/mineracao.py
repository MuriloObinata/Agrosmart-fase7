"""Mineração de processos sobre o event log do AgroSmart (logs/event_log.csv).
Gera em relatorios/: variantes.csv, arestas_dfg.csv, metricas.json, processo.dot,
processo.png e analise_processo.md.
Cada leitura de sensor é um "case_id"; cada etapa do pipeline é uma "atividade"."""

import argparse
import json
import os
import shutil
import subprocess  # nosec B404 - só chama o executável 'dot' do Graphviz
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ORDEM = [
    "Leitura coletada", "Leitura recebida", "Leitura validada", "Leitura rejeitada",
    "Leitura transformada", "Risco classificado", "Alerta emitido",
    "Saídas publicadas", "Rejeição registrada",
]
CORES = {
    "Leitura rejeitada": "#F8D7DA", "Rejeição registrada": "#F8D7DA",
    "Alerta emitido": "#FFE8B3", "Saídas publicadas": "#D4EDDA",
}


def formatar_tempo(segundos):
    if segundos < 0.001:
        return "<1 ms"
    if segundos < 1:
        return f"{segundos * 1000:.0f} ms"
    if segundos < 120:
        return f"{segundos:.1f} s"
    return f"{segundos / 60:.1f} min"


def carregar_tracos(eventos):
    """Agrupa os eventos por caso, em ordem de tempo, em caso de "empate", é decidido por etapa."""
    eventos = eventos.copy()
    eventos["ordem"] = eventos["atividade"].map(ORDEM.index)
    eventos = eventos.sort_values(["case_id", "timestamp", "ordem"], kind="stable")
    return {
        caso: list(zip(g["atividade"], g["timestamp"], g["resultado"].fillna(""), strict=True))
        for caso, g in eventos.groupby("case_id", sort=False)
    }


def analisar(eventos):
    tracos = carregar_tracos(eventos)
    variantes = Counter(tuple(a for a, _, _ in t) for t in tracos.values())

    duracoes = defaultdict(list)
    lead_times = []
    for passos in tracos.values():
        for (a, ta, _), (b, tb, _) in zip(passos, passos[1:], strict=False):
            duracoes[(a, b)].append((tb - ta).total_seconds())
        lead_times.append((passos[-1][1] - passos[0][1]).total_seconds())

    arestas = pd.DataFrame(
        [
            {
                "origem": a, "destino": b, "frequencia": len(v),
                "media_s": sum(v) / len(v),
                "mediana_s": float(pd.Series(v).median()),
                "p95_s": float(pd.Series(v).quantile(0.95)),
                "max_s": max(v),
                "total_s": sum(v),
            }
            for (a, b), v in duracoes.items()
        ]
    ).sort_values("frequencia", ascending=False)

    por_atividade = eventos["atividade"].value_counts().to_dict()
    motivos = (
        eventos.loc[eventos["atividade"] == "Leitura rejeitada", "resultado"].value_counts().to_dict()
    )
    niveis = (
        eventos.loc[eventos["atividade"] == "Risco classificado", "resultado"].value_counts().to_dict()
    )
    return tracos, variantes, arestas, lead_times, por_atividade, motivos, niveis


def escrever_dot(arquivo, arestas, por_atividade):
    maior = max(arestas["frequencia"].max(), 1)
    linhas = [
        "digraph AgroSmart {",
        '  rankdir=TB; bgcolor="white"; nodesep=0.4; ranksep=0.5;',
        '  node [shape=box, style="rounded,filled", fillcolor="#E8F0FE", color="#4A6FA5", '
        'fontname="Helvetica", fontsize=11];',
        '  edge [fontname="Helvetica", fontsize=9, color="#555555"];',
    ]
    for atividade in ORDEM:
        if atividade in por_atividade:
            cor = CORES.get(atividade, "#E8F0FE")
            linhas.append(
                f'  "{atividade}" [label="{atividade}\\n(n={por_atividade[atividade]})", fillcolor="{cor}"];'
            )
    for _, r in arestas.iterrows():
        largura = 1 + 4 * r["frequencia"] / maior
        rotulo = f'{int(r["frequencia"])}x | média {formatar_tempo(r["media_s"])}'
        linhas.append(
            f'  "{r["origem"]}" -> "{r["destino"]}" [label="{rotulo}", penwidth={largura:.1f}];'
        )
    linhas.append("}")
    arquivo.write_text("\n".join(linhas), encoding="utf-8")


def renderizar(dot_arquivo, png_arquivo):
    executavel = shutil.which("dot")
    if not executavel:
        return False
    subprocess.run(  # nosec B603 - argumentos fixos sem entrada do usuário
        [executavel, "-Tpng", "-Gdpi=130", str(dot_arquivo), "-o", str(png_arquivo)], check=True
    )
    return True


CURTOS = {
    "Leitura coletada": "Coletada", "Leitura recebida": "Recebida", "Leitura validada": "Validada",
    "Leitura rejeitada": "Rejeitada", "Leitura transformada": "Transformada",
    "Risco classificado": "Classificada", "Alerta emitido": "Alerta emitido",
    "Saídas publicadas": "Publicada", "Rejeição registrada": "Rejeição registrada",
}


def caminho_legivel(caminho):
    return " → ".join(CURTOS.get(a, a) for a in caminho)


def gerar_relatorio(base=".", saida=None):
    base = Path(base)
    saida = Path(saida) if saida else base / "relatorios"
    saida.mkdir(parents=True, exist_ok=True)
    eventos = pd.read_csv(base / "logs" / "event_log.csv", parse_dates=["timestamp"])
    if eventos.empty:
        raise SystemExit("event_log.csv está vazio: rode o pipeline antes da mineração.")

    tracos, variantes, arestas, lead_times, por_atividade, motivos, niveis = analisar(eventos)
    total = len(tracos)

    tabela_variantes = pd.DataFrame(
        [
            {"variante": f"V{i}", "casos": n, "pct": round(100 * n / total, 1),
             "caminho": caminho_legivel(c)}
            for i, (c, n) in enumerate(variantes.most_common(), start=1)
        ]
    )
    tabela_variantes.to_csv(saida / "variantes.csv", index=False)
    arestas.round(4).to_csv(saida / "arestas_dfg.csv", index=False)
    escrever_dot(saida / "processo.dot", arestas, por_atividade)
    png = renderizar(saida / "processo.dot", saida / "processo.png")

    # ---- gargalo: aresta que concentra mais tempo total
    arestas_t = arestas.sort_values("total_s", ascending=False)
    soma = arestas["total_s"].sum() or 1
    top = arestas_t.iloc[0]
    parcela = 100 * top["total_s"] / soma
    lead = pd.Series(lead_times)

    metricas = {
        "casos": total,
        "eventos": int(len(eventos)),
        "variantes": len(variantes),
        "periodo_inicio": str(eventos["timestamp"].min()),
        "periodo_fim": str(eventos["timestamp"].max()),
        "lead_time_medio_s": round(float(lead.mean()), 3),
        "lead_time_p95_s": round(float(lead.quantile(0.95)), 3),
        "gargalo": f"{top['origem']} -> {top['destino']}",
        "gargalo_media_s": round(float(top["media_s"]), 3),
        "gargalo_parcela_pct": round(float(parcela), 1),
        "rejeicao_pct": round(100 * por_atividade.get("Leitura rejeitada", 0) / total, 1),
        "motivos_rejeicao": motivos,
        "niveis_risco": niveis,
        "alertas": por_atividade.get("Alerta emitido", 0),
    }
    (saida / "metricas.json").write_text(
        json.dumps(metricas, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    escrever_markdown(saida / "analise_processo.md", metricas, tabela_variantes, arestas_t, png)
    return metricas, saida


def escrever_markdown(arquivo, m, variantes, arestas, png):
    aceitos = m["niveis_risco"]
    niveis_txt = ", ".join(f"{k}: {v}" for k, v in aceitos.items())
    motivos_txt = ", ".join(f"{k}: {v}" for k, v in m["motivos_rejeicao"].items()) or "nenhum"
    n_aceitos = sum(aceitos.values()) or 1
    pct_alerta = round(100 * m["alertas"] / n_aceitos, 1)
    linhas = [
        "# Análise de processo (mineração de processos) - AgroSmart",
        "",
        f"Event log: **{m['casos']} casos** (1 caso = 1 leitura de sensor), **{m['eventos']} eventos**, "
        f"**{m['variantes']} variantes**, de {m['periodo_inicio']} a {m['periodo_fim']}.",
        "Tempo de ponta a ponta por leitura (coleta → publicação): "
        f"média **{formatar_tempo(m['lead_time_medio_s'])}**, "
        f"p95 **{formatar_tempo(m['lead_time_p95_s'])}**.",
        "",
        "## Representação do processo",
        "",
        "![Mapa do processo](processo.png)" if png else "(PNG não gerado: use processo.dot)",
        "",
        "## Variantes (caminhos percorridos)",
        "",
        "| Variante | Casos | % | Caminho |",
        "|---|---|---|--,-|"
    ]
    for _, r in variantes.iterrows():
        linhas.append(f"| {r['variante']} | {r['casos']} | {r['pct']} | {r['caminho']} |")
    linhas += [
        "",
        f"Leituras por nível de risco: {niveis_txt}.",
        f"Motivos de rejeição: {motivos_txt}.",
        "",
        "## Tempos entre etapas",
        "",
        "| De | Para | Casos | Média | P95 | Máx. |",
        "|---|---|---|---|---|---|",
    ]
    for _, r in arestas.iterrows():
        linhas.append(
            f"| {r['origem']} | {r['destino']} | {int(r['frequencia'])} | {formatar_tempo(r['media_s'])} "
            f"| {formatar_tempo(r['p95_s'])} | {formatar_tempo(r['max_s'])} |"
        )
    linhas += [
        "",
        "## Insights",
        "",
        f"**Gargalo.** A transição *{m['gargalo'].replace(' -> ', '* → *')}* concentra "
        f"{m['gargalo_parcela_pct']}% de todo o tempo gasto entre etapas (média de "
        f"{formatar_tempo(m['gargalo_media_s'])}). As etapas de validação, transformação e "
        "classificação levam milissegundos: o tempo é de **espera**, não de processamento, "
        "porque o pipeline trabalha em lotes (ciclos) e a leitura aguarda o próximo ciclo.",
        "",
        f"**Variações.** O caminho mais comum ({variantes.iloc[0]['pct']}% dos casos) é: "
        f"{variantes.iloc[0]['caminho']}. As demais variantes são desvios: leituras rejeitadas "
        f"({m['rejeicao_pct']}% dos casos) e leituras com alerta emitido "
        f"({pct_alerta}% das leituras aceitas).",
        "",
        "**Oportunidades de melhoria.**",
        "",
        "1. Reduzir a espera entre coleta e processamento: diminuir o intervalo do ciclo ou "
        "processar por evento (fila/MQTT), eliminando a espera do lote.",
        "2. Fechar o fluxo do alerta: hoje o processo termina em 'Saídas publicadas'. Não há "
        "evento de ação executada (irrigação ligada, ventilação acionada); registrar esse evento "
        "permitiria medir o tempo entre alerta e ação.",
        f"3. Tratar o volume de alertas ({pct_alerta}% das leituras aceitas): usar tolerância "
        "(por exemplo, exigir 2 leituras seguidas fora da faixa) para reduzir alertas por ruído.",
        f"4. Monitorar a taxa de rejeição ({m['rejeicao_pct']}%): uma alta persistente indica "
        "sensor defeituoso ou problema de transmissão, e pode virar um alerta próprio.",
        "",
        "> Observação: as leituras vêm de um simulador; os tempos refletem o ambiente de teste.",
    ]
    arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Mineração de processos do AgroSmart")
    parser.add_argument("--base", default=os.environ.get("AGROSMART_BASE", "."))
    args = parser.parse_args()
    metricas, saida = gerar_relatorio(args.base)
    print(json.dumps(metricas, ensure_ascii=False, indent=2))
    print(f"\nArquivos gerados em {saida}")


if __name__ == "__main__":
    main()
