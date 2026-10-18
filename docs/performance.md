# Ensaios locais de desempenho

Data: 17/10/2026.

Ambiente: Windows, Docker Desktop, PostgreSQL e API Uvicorn com `--reload`.

Cada execução enviou 200 solicitações com oito clientes concorrentes.
Alvo interno do experimento: resposta válida em até 2.000 ms.

## Política sentinel24-1.4

Medições realizadas após implementar a referência individual de frequência.

| Cenário | Histórico por pagador | Duração | Decisões válidas/s | p50 (ms) | p95 (ms) | p99 (ms) | Máximo (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mesmo pagador | 0 | 7,30 s | 27,41 | 262,04 | 442,65 | 505,85 | 522,50 |
| Pagadores diferentes | 100 | 8,02 s | 24,93 | 299,95 | 540,81 | 683,46 | 698,48 |

As 400 solicitações produziram respostas HTTP 201 válidas, sem erros reportados e dentro do alvo de dois segundos.

No cenário sem histórico do mesmo pagador, houve sete aprovações e 193 recomendações de segundo fator. Isso verifica a permanência da política inicial quando não há referência histórica suficiente.

O cenário com histórico preparou 20.000 registros sintéticos antes da medição e produziu 200 aprovações. O benchmark exigiu perfil de valores maduro e comparação geográfica avaliada.

Esses dados representam comportamento conhecido preparado para o ensaio. Não constituem uma base rotulada para avaliar detecção de fraude.

A adaptação da frequência, os limites efetivos e as proteções da referência também são verificados pelos testes unitários e de integração.

## Histórico: política sentinel24-1.2

Estas medições foram realizadas antes das alterações de política.

| Execução | Cenário | Histórico por pagador | p95 (ms) | p99 (ms) | Máximo (ms) | Decisões válidas/s |
|---|---|---:|---:|---:|---:|---:|
| 1 | Pagadores diferentes | 0 | 690,26 | 1.943,55 | 1.949,85 | 20,13 |
| 2 | Mesmo pagador | 0 | 423,67 | 452,31 | 456,90 | 26,95 |
| 3 | Mesmo pagador, repetição | 0 | 393,31 | 441,91 | 453,20 | 29,64 |
| 4 | Pagadores diferentes, repetição | 0 | 504,69 | 552,81 | 587,39 | 25,51 |
| 5 | Pagadores diferentes | 12 | 664,21 | 721,38 | 730,06 | 24,62 |
| 6 | Pagadores diferentes | 100 | 950,80 | 1.073,05 | 1.213,89 | 12,07 |

As 1.200 solicitações produziram respostas HTTP 201 válidas, sem erros reportados e dentro do alvo de dois segundos.

Nos dois ensaios do mesmo pagador, cada execução produziu sete aprovações e 193 recomendações de segundo fator.

Os ensaios com histórico prepararam, antes da medição, 2.400 e 20.000 registros sintéticos.

## Reprodução

Com API e banco em execução:

```bash
python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent
python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload same_payer
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent --history 12
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent --history 100
```

Cada execução cria novos identificadores e informa a versão de política utilizada.

## Método e limitações

O tempo é observado pelo cliente HTTP, com concorrência fixa e percentis calculados pelo método nearest rank.

A medição inclui comunicação e leitura da resposta. A preparação da massa histórica fica fora do tempo medido.

Respostas com `two_factor_required` ou `rejected` são decisões válidas, não erros técnicos. Os ensaios não medem transferências financeiras executadas.

Cada execução aumenta a base. Aquecimento, cache, ordem das rodadas e carga da máquina podem influenciar os resultados.

As diferenças entre versões não demonstram, isoladamente, ganho ou perda causado pela alteração do código: não foi realizado um experimento controlado para estabelecer essa relação.

Os resultados não demonstram capacidade máxima, SLA de produção ou taxa de detecção de fraude.

Os testes sintéticos verificam coerência com os cenários definidos; não substituem validação com dados reais e confirmação de legitimidade.