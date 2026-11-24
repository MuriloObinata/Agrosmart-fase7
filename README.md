# AgroSmart – Fase 7

O AgroSmart tem o objetivo de ser um monitor de estufas. O sensor (simulado) envia leituras de **temperatura**, **umidade do solo** e **luminosidade**. O pipeline valida os dados recebidos, aplica as regras de decisão e gera alertas com recomendações. Depois disso, ele publica os arquivos que alimentam o dashboard no Power BI.

**Equipe:** 
- Lucas Sobral Roxo (RM98188)  
- Maitê Savicius Menezes (RM98435) 
- Murilo Henrique Obinata (RM99855) 
- Pedro Henrique Oliveira Ananias (RM550689) 
- Vitor da Silva Sant´ana (RM99586) 

FIAP 4ESOA.

## Fluxo da solução

```
Simulador de sensor ──► data/raw (JSONL) ──► Pipeline ──► data/trusted ──► data/refined ──► Power BI
 (container)             camada RAW          validação     camada TRUSTED    regras + alertas   (dashboard)
                                             (container)
                                                │
                                                ├─► logs/agrosmart.log     log da aplicação
                                                ├─► logs/status.json       estado da última execução
                                                ├─► logs/execucoes.csv     indicadores por ciclo
                                                └─► logs/event_log.csv     event log (mineração de processos)
```

| Componente | Arquivo | O que faz |
|---|---|---|
| Simulador | `src/agrosmart/simulador.py` | Gera uma leitura a cada 2 s e cria falhas propositais |
| Etapas | `src/agrosmart/etapas.py` | Lê o JSON, converte unidades, valida dados e cria campos derivados |
| Regras | `src/agrosmart/regras.py` | Define faixas ideais (28–32 °C e 40–70 %), nível de risco e recomendação |
| Pipeline | `src/agrosmart/pipeline.py` | Roda o ciclo contínuo entre RAW, TRUSTED e REFINED, além de gravar logs, status e event log |
| Healthcheck | `src/agrosmart/healthcheck.py` | Informa ao Docker se o serviço está saudável |
| Mineração | `src/agrosmart/mineracao.py` | Analisa o event log e gera o mapa do processo |
| Histórico | `src/agrosmart/historico.py` | Reproduz a base de 2 dias da Fase 6 |

## Como executar (Docker)

```bash
docker compose up --build -d      # sobe simulador + pipeline
docker compose ps                 # STATUS "healthy" = funcionando
docker compose logs -f pipeline   # log em tempo real
docker compose down               # desliga
```

Logo os arquivos `leituras_analisadas.csv` e `alertas.csv` aparecem na pasta `data/refined/`, que será usado no Power BI. Os arquivos de monitoramento ficam na pasta `logs/`.

### Mineração de processos

```bash
docker compose --profile analise run --rm mineracao
```

Esse comando salva cinco arquivos na pasta `relatorios/`. O `processo.png` mostra o mapa do processo. Os arquivos `variantes.csv`, `arestas_dfg.csv` e `metricas.json` trazem os dados estruturados. Já o `analise_processo.md` aponta gargalos, variações e possíveis melhorias.

### Sem Docker (Python 3.12+)

```bash
pip install -r requirements-dev.txt
$env:PYTHONPATH = "src"                            # PowerShell (Linux/macOS: export PYTHONPATH=src)
python -m agrosmart.simulador                       # terminal 1
python -m agrosmart.pipeline --intervalo 30         # terminal 2
pytest -v                                           # testes
```

Para rodar a base de 2 dias da Fase 6, execute `python -m agrosmart.historico` e depois `python -m agrosmart.pipeline --once --reset`. O resultado esperado tem 98 linhas lidas, 93 aceitas e 5 rejeitadas.

## Exemplo de entrada e saída

Entrada em `data/raw/sensor_stream.jsonl`

```json
{"device": "Sensor_001", "location": "Estufa A", "date": "21/11/2026", "time": "14:32:10", "temperature": "34,2 C", "reading": {"light": 812, "soil_moisture": "31%"}}
```

Saída em `data/refined/leituras_analisadas.csv`

O campo `nivel_risco` fica como `CRÍTICO` porque a temperatura está acima da faixa e o solo está abaixo. O campo `recomendacao` recebe o texto `Temperatura alta: acionar ventilação ou sombreamento. | Solo seco: acionar irrigação.`.

## Monitoramento

| O que | Onde | Como acompanhar |
|---|---|---|
| Container saudável | `docker compose ps` | Coluna STATUS mostra `healthy` ou `unhealthy` |
| Log da aplicação | `logs/agrosmart.log` ou `docker compose logs -f pipeline` | Linha `ciclo=... lidos=... aceitos=... rejeitados=...` e avisos de alerta ou rejeição |
| Status da execução | `logs/status.json` | Estado, último ciclo, totais, taxa de rejeição e leituras por nível |
| Indicadores por ciclo | `logs/execucoes.csv` | Tabela usada na página de monitoramento do Power BI |

O healthcheck marca o pipeline como `unhealthy` em dois casos. O primeiro acontece se o arquivo `status.json` ficar mais de 3 ciclos sem atualizar. O segundo ocorre se o último ciclo terminar com erro.

## Pipeline DevSecOps (`.github/workflows/ci.yml`)

A cada push, o GitHub Actions roda cinco etapas.

**Qualidade de código** O comando `ruff check` verifica a padronização do código.

**Testes automatizados** O `pytest` roda 32 testes que cobrem regras, validação, pipeline, event log e healthcheck.

**Segurança do código** O `bandit` busca falhas de severidade média ou alta, e o `pip-audit` verifica as dependências.

**Build e execução em container** O workflow roda `docker build` e `docker compose up`, espera o healthcheck ficar `healthy`, coleta status e logs e executa a mineração de processos dentro do container.

**Vulnerabilidades da imagem** O Trivy gera um relatório de falhas HIGH e CRITICAL. A build falha se existir alguma falha CRITICAL com correção disponível. A ação do Trivy está fixada por commit (SHA) por causa do incidente de segurança nas tags em março de 2026.

## Estrutura

```
src/agrosmart/ código da aplicação tests/ testes automatizados
data/ raw / trusted / refined logs/ monitoramento e event log
relatorios/ saída da mineração docs/ documentação da entrega
Dockerfile - docker-compose.yml - requirements*.txt - .github/workflows/ci.yml
``` 
