# Ensaios locais de desempenho

Medições realizadas em 17 e 18/10/2026.

Ambiente: Windows, Docker Desktop, PostgreSQL e API Uvicorn com `--reload`.

Cada execução enviou 200 solicitações com oito clientes concorrentes.

Alvo experimental interno: resposta válida em até 2.000 ms. Esse alvo não representa um SLA garantido.

## Política sentinel24-1.5 — dispositivo compartilhado

O cenário utiliza pagadores diferentes associados ao mesmo dispositivo, com 100 registros históricos por pagador.

Antes de cada medição, foram preparados 20.000 registros sintéticos. As solicitações disputam o bloqueio do dispositivo compartilhado.

As primeiras execuções revelaram falhas temporárias HTTP 503 sob contenção. Essas respostas foram contabilizadas como erros técnicos, sem convertê-las em decisões antifraude.

| Execução | Alteração acumulada | Duração | Decisões válidas/s | HTTP 201 | HTTP 503 | p95 (ms) | p99 (ms) | Máximo (ms) | Válidas em até 2 s |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | Consulta inicial de relacionamentos | 40,86 s | 3,28 | 134 | 66 | 2.001,05 | 2.124,27 | 2.156,24 | 123 |
| B | Consulta agregada em uma passagem | 28,12 s | 7,08 | 199 | 1 | 1.546,57 | 1.611,39 | 1.754,24 | 199 |
| C | Índice de cobertura e agregação por dia e titular | 25,29 s | 7,83 | 198 | 2 | 1.663,06 | 1.778,42 | 1.832,61 | 198 |
| D | Preparação passou a executar ANALYZE antes da medição | 20,92 s | 9,56 | 200 | 0 | 1.372,63 | 1.833,96 | 2.170,26 | 199 |

Todas as decisões válidas dessas execuções foram `approved`, conforme o histórico sintético preparado.

Na execução D:

- p50: 899,73 ms.
- Histórico de relacionamento com dispositivo avaliado em 197 respostas.
- 200 respostas válidas e nenhum erro reportado.
- 199 respostas dentro do alvo interno de dois segundos.
- Uma resposta acima do alvo, com 2.170,26 ms.

Os critérios das regras e os bloqueios foram preservados durante as otimizações.

### Consultas e estatísticas

A consulta de relacionamentos agrega as observações por titular e dia e depois verifica a maturidade de cada relacionamento.

O índice `pix_analyses_device_context_idx` utiliza `device_id` e `received_at` como chaves e inclui `source_document`, `status`, `amount` e `pix_id`.

Um plano observado no PostgreSQL confirmou o uso desse índice com `Index Only Scan`. A existência do índice não garante que todas as execuções evitem acesso à tabela.

A inserção concentrada de registros sintéticos pode deixar as estimativas do planejador desatualizadas. A preparação do benchmark passou a executar `ANALYZE pix_analyses` após inserir o histórico e antes de iniciar a medição HTTP.

Essa atualização faz parte da preparação do experimento. Não é executada pela API a cada solicitação.

A execução D possui uma diferença de método em relação às anteriores. Cache, crescimento da base, carga da máquina e ordem das rodadas também variaram. Os resultados não isolam quanto cada alteração contribuiu para o desempenho.

## Política sentinel24-1.4

Medições realizadas após implementar a referência individual de frequência.

| Cenário | Histórico por pagador | Duração | Decisões válidas/s | p50 (ms) | p95 (ms) | p99 (ms) | Máximo (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mesmo pagador | 0 | 7,30 s | 27,41 | 262,04 | 442,65 | 505,85 | 522,50 |
| Pagadores diferentes | 100 | 8,02 s | 24,93 | 299,95 | 540,81 | 683,46 | 698,48 |

As 400 solicitações produziram respostas HTTP 201 válidas, sem erros reportados e dentro do alvo de dois segundos.

No cenário sem histórico do mesmo pagador, houve sete aprovações e 193 recomendações de segundo fator.

O cenário com histórico preparou 20.000 registros sintéticos e produziu 200 aprovações. O benchmark exigiu perfil de valores maduro e comparação geográfica avaliada.

## Histórico: política sentinel24-1.2

Estas medições foram realizadas antes das alterações posteriores de política.

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
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload shared_device --history 100
```

Cada execução cria novos identificadores e informa a versão de política utilizada.

A versão atual do script executa `ANALYZE` na preparação dos cenários com histórico. Ela não reproduz exatamente o método das medições antigas.

## Método e limitações

O benchmark utiliza concorrência fixa: cada cliente envia sua próxima solicitação após concluir a anterior. Não simula uma taxa externa constante de chegada.

O tempo é observado pelo cliente HTTP e inclui comunicação e leitura da resposta. Preparação do histórico e atualização de estatísticas ficam fora da medição.

Os percentis usam o método nearest rank e consideram todas as requisições medidas, inclusive respostas de erro.

Respostas com `two_factor_required` ou `rejected` são decisões válidas. HTTP 503 e falhas de transporte são erros técnicos e não entram na taxa de decisões válidas.

O script retorna código de saída diferente de zero quando encontra erros. Ultrapassar o orçamento de latência, isoladamente, não altera esse código; é necessário consultar `valid_within_budget`.

Cada execução aumenta a base. Aquecimento, cache, estatísticas, ordem das rodadas e carga da máquina podem influenciar os resultados.

As diferenças entre versões não demonstram, isoladamente, ganho ou perda causado por uma alteração específica.

Os dados foram preparados para exercitar os caminhos de consulta. Não constituem uma base rotulada para avaliar detecção de fraude.

Os resultados não demonstram capacidade máxima, SLA de produção, taxa de detecção ou taxa de falsos positivos.