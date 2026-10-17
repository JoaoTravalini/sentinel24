# Ensaios locais de desempenho

Data: 17/10/2026. Política: `sentinel24-1.2`.

Ambiente: Windows, Docker Desktop, PostgreSQL e API Uvicorn com `--reload`.
Cada execução enviou 200 solicitações com oito clientes concorrentes.
Alvo interno do experimento: resposta válida em até 2.000 ms.

| Execução | Cenário | Histórico por pagador | p95 (ms) | p99 (ms) | Máximo (ms) | Decisões válidas/s |
|---|---|---:|---:|---:|---:|---:|
| 1 | Pagadores diferentes | 0 | 690,26 | 1.943,55 | 1.949,85 | 20,13 |
| 2 | Mesmo pagador | 0 | 423,67 | 452,31 | 456,90 | 26,95 |
| 3 | Mesmo pagador, repetição | 0 | 393,31 | 441,91 | 453,20 | 29,64 |
| 4 | Pagadores diferentes, repetição | 0 | 504,69 | 552,81 | 587,39 | 25,51 |
| 5 | Pagadores diferentes | 12 | 664,21 | 721,38 | 730,06 | 24,62 |
| 6 | Pagadores diferentes | 100 | 950,80 | 1.073,05 | 1.213,89 | 12,07 |

As 1.200 solicitações produziram respostas HTTP 201 válidas, sem erros
reportados e dentro do alvo de dois segundos.

Nos dois ensaios do mesmo pagador, cada execução produziu sete aprovações
e 193 recomendações de segundo fator, conforme a regra de frequência.

Os ensaios com histórico prepararam, antes da medição, 2.400 e 20.000
registros sintéticos. O benchmark exigiu perfil maduro e comparação
geográfica avaliada em todas as respostas consideradas válidas.

Esses dados simulam comportamento conhecido; não avaliam acurácia antifraude.

## Reprodução

```bash
python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent
python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload same_payer
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent --history 12
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent --history 100
```

## Limites da medição

O tempo é observado pelo cliente HTTP, com concorrência fixa e percentis
calculados pelo método nearest rank. Inclui comunicação e leitura da
resposta; a preparação da massa histórica fica fora da medição.

Cada execução cria novos identificadores e aumenta a base. Aquecimento,
cache e carga da máquina também podem influenciar as diferenças entre rodadas.

Os resultados não demonstram capacidade máxima, SLA de produção ou taxa
de detecção de fraude. O pico inicial próximo de dois segundos não se
repetiu na segunda execução de pagadores diferentes; sua causa não foi isolada.