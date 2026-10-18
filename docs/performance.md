# Ensaios locais de desempenho

A política atual é `sentinel24-1.10`. A medição mais recente utiliza `accumulated_amount` nessa versão. Os ensaios anteriores permanecem identificados por sua política e não constituem novas medições da 1.10.

Medições realizadas em 17 e 18/10/2026, em Windows com Docker Desktop, PostgreSQL 16 e API Uvicorn com `--reload`.

Alvo experimental interno: decisão válida em até 2.000 ms. Esse alvo não representa um SLA garantido nem um prazo oficial de liquidação Pix.

## Resultado mais recente: política sentinel24-1.10

Cenário `accumulated_amount`: 200 pagadores, destinatários e dispositivos independentes; concorrência oito. Cada titular recebe 100 registros históricos de R$ 100 e dois pagamentos recentes de R$ 900, observados 20 e 40 minutos antes do instante de preparação. A solicitação medida é outro pagamento de R$ 900.

Foram preparados 20.400 registros sintéticos antes da medição HTTP. A preparação executou ANALYZE, sem VACUUM explícito. As 200 respostas confirmaram perfil de valores maduro, comparação geográfica avaliada e referência de acúmulo avaliada e madura. Também confirmaram score 35 somente em `velocity`, os sinais `ACCUMULATED_AMOUNT_1H` e `ACCUMULATED_AMOUNT_24H` e status `two_factor_required`.

| Medida | Resultado |
|---|---:|
| Solicitações / concorrência | 200 / 8 |
| Histórico preparado | 20.400 |
| HTTP 201 válidos | 200 |
| Erros reportados | 0 |
| Referência de acúmulo avaliada e madura | 200 respostas |
| Decisões válidas em até 2.000 ms | 200 |
| Duração HTTP | 12,31 s |
| Decisões válidas/s | 16,24 |
| p50 | 425,85 ms |
| p95 | 883,21 ms |
| p99 | 1.572,60 ms |
| Máximo | 1.609,77 ms |

Os documentos dos pagadores são CNPJ sintéticos; o limite noturno PF para PF não se aplica a esse cenário. Os perfis de compartilhamento de dispositivo e concentração do destinatário não foram avaliados. Esse ensaio exercita o caminho de acúmulo com histórico maduro; não é um teste de todas as regras simultaneamente nem um cenário de contenção no mesmo destinatário.

Não houve erro ou resposta acima do alvo interno nessa execução. O resultado não caracteriza capacidade máxima, SLA ou precisão antifraude e não substitui os ensaios históricos de contenção.

## Histórico: política sentinel24-1.6

O cenário `same_recipient` utiliza pagadores e dispositivos diferentes associados ao mesmo destinatário. Cada execução prepara 100 registros históricos por pagador, totalizando 20.000 registros sintéticos novos antes da medição HTTP. As solicitações disputam o bloqueio transacional do destinatário.

| Medida | Concorrência 4 | Concorrência 8 |
|---|---:|---:|
| Solicitações | 200 | 200 |
| Histórico preparado por execução | 20.000 | 20.000 |
| HTTP 201 válidos | 200 | 197 |
| HTTP 503 | 0 | 3 |
| Decisões válidas em até 2 s | 200 | 197 |
| Perfil do destinatário avaliado | 193 respostas | 190 respostas |
| Duração HTTP | 26,42 s | 25,41 s |
| Decisões válidas/s | 7,57 | 7,75 |
| p50 | 457,12 ms | 942,56 ms |
| p95 | 943,38 ms | 1.502,53 ms |
| p99 | 1.437,98 ms | 1.672,46 ms |
| Máximo | 1.522,71 ms | 1.921,00 ms |

Todas as decisões válidas foram `approved`, conforme o histórico sintético preparado. O perfil de compartilhamento de dispositivo não foi avaliado porque os dispositivos eram diferentes.

Com concorrência quatro, as 200 solicitações concluíram sem erro e dentro do alvo interno nessa execução. Com concorrência oito, três solicitações não produziram decisão válida. Reduzir a concorrência não demonstra que o cenário de oito tenha sido resolvido nem estabelece capacidade máxima.

As sete análises iniciais de cada rodada não precisaram consultar o perfil histórico do destinatário: a atividade recente ainda não atingia o limiar para essa consulta.

## Evolução dos ensaios da política 1.6

A a F utilizaram concorrência oito; G utilizou quatro. Todos prepararam 20.000 registros para `same_recipient`.

| Execução | Configuração acumulada | Duração (s) | Válidas/s | HTTP 201 | HTTP 503 | Válidas em até 2 s |
|---|---|---:|---:|---:|---:|---:|
| A | Consulta inicial de referência | 43,31 | 3,42 | 148 | 52 | 137 |
| B | Leitura única do histórico e agrupamento por instante para os picos | 32,31 | 5,94 | 192 | 8 | 192 |
| C | Índice parcial de cobertura do destinatário | 35,39 | 5,37 | 190 | 10 | 190 |
| D | VACUUM ANALYZE explícito na preparação | 31,69 | 6,12 | 194 | 6 | 193 |
| E | Agrupamento por instante e dia observado antes da cobertura histórica | 25,87 | 7,61 | 197 | 3 | 197 |
| F | Estrutura SQL de atividade reutilizada; VACUUM sem trabalhadores paralelos | 25,41 | 7,75 | 197 | 3 | 197 |
| G | Mesma implementação de F, concorrência quatro | 26,42 | 7,57 | 200 | 0 | 200 |

| Execução | p50 (ms) | p95 (ms) | p99 (ms) | Máximo (ms) | Perfil do destinatário avaliado |
|---|---:|---:|---:|---:|---:|
| A | 1.651,64 | 2.019,67 | 2.570,18 | 2.960,24 | 141 |
| B | 1.388,89 | 1.722,12 | 1.869,68 | 1.911,47 | 185 |
| C | 1.492,34 | 1.766,87 | 1.823,58 | 1.851,36 | 183 |
| D | 1.346,04 | 1.701,88 | 2.330,24 | 2.434,36 | 187 |
| E | 1.068,64 | 1.601,99 | 1.760,91 | 1.855,88 | 190 |
| F | 942,56 | 1.502,53 | 1.672,46 | 1.921,00 | 190 |
| G | 457,12 | 943,38 | 1.437,98 | 1.522,71 | 193 |

As decisões válidas foram `approved`; HTTP 503 foi contabilizado como erro técnico.

Uma execução intermediária, após a reutilização da estrutura SQL, falhou durante o VACUUM de preparação por falta de espaço em um segmento de memória compartilhada. Ela não chegou à fase HTTP e não gerou medidas de latência ou vazão. A manutenção opcional do script passou a utilizar `VACUUM (ANALYZE, PARALLEL 0) pix_analyses`.

### Diagnóstico e alterações

Logs do PostgreSQL de um ensaio com seis respostas HTTP 503 mostraram seis cancelamentos por `statement_timeout` no comando `pg_advisory_xact_lock`. O identificador do bloqueio compartilhado do destinatário aparecia em todos eles, confirmando contenção naquele ensaio.

Os bloqueios e os limites de espera foram preservados. As alterações buscaram reduzir o trabalho realizado enquanto a análise mantém os bloqueios:

- A consulta histórica reutiliza a leitura elegível nos cálculos dos dois relógios.
- Registros com o mesmo instante e dia observado são agrupados com multiplicidade e datas extremas preservadas. Não há arredondamento dos instantes nem descarte de transações repetidas no mesmo horário.
- A estrutura SQL das sete consultas de atividade é construída uma vez. Cada execução recebe novos titulares, dispositivo, identificador Pix e limites temporais. Resultados e históricos não são guardados em cache por esse mecanismo.

O índice parcial `pix_analyses_recipient_reference_idx` utiliza `destination_document` e `received_at`, inclui `transaction_at` e `pix_id` e abrange análises com status `approved`.

Em uma consulta isolada observada no PostgreSQL:

| Medida | Antes do VACUUM | Após o VACUUM |
|---|---:|---:|
| Tempo da consulta | 122,179 ms | 47,595 ms |
| Heap Fetches no índice de referência | 10.400 | 0 |

O plano mostrou acesso somente pelo índice após a manutenção nesse conjunto de dados. Essa medição não representa a latência HTTP nem garante que consultas futuras evitem acesso à tabela.

## Histórico: política sentinel24-1.5

O cenário `shared_device` utiliza pagadores diferentes associados ao mesmo dispositivo, com 100 registros históricos por pagador. Cada execução preparou 20.000 registros; as solicitações disputaram o bloqueio do dispositivo.

| Execução | Alteração acumulada | Duração (s) | Válidas/s | HTTP 201 | HTTP 503 | Válidas em até 2 s |
|---|---|---:|---:|---:|---:|---:|
| A | Consulta inicial de relacionamentos | 40,86 | 3,28 | 134 | 66 | 123 |
| B | Consulta agregada em uma passagem | 28,12 | 7,08 | 199 | 1 | 199 |
| C | Índice de cobertura e agregação por dia e titular | 25,29 | 7,83 | 198 | 2 | 198 |
| D | ANALYZE explícito na preparação | 20,92 | 9,56 | 200 | 0 | 199 |

| Execução | p50 (ms) | p95 (ms) | p99 (ms) | Máximo (ms) | Histórico de dispositivo avaliado |
|---|---:|---:|---:|---:|---:|
| A | 1.558,55 | 2.001,05 | 2.124,27 | 2.156,24 | 131 |
| B | 1.237,44 | 1.546,57 | 1.611,39 | 1.754,24 | 196 |
| C | 1.056,65 | 1.663,06 | 1.778,42 | 1.832,61 | 195 |
| D | 899,73 | 1.372,63 | 1.833,96 | 2.170,26 | 197 |

Todas as decisões válidas foram `approved`. A execução D teve uma resposta acima do alvo interno, mesmo sem erros HTTP.

A consulta agrega observações por titular e dia antes de verificar a maturidade dos relacionamentos. O índice `pix_analyses_device_context_idx` utiliza `device_id` e `received_at` e inclui `source_document`, `status`, `amount` e `pix_id`. Um plano observado confirmou seu uso com `Index Only Scan`.

Estes resultados pertencem à política 1.5; não são novas medições da política 1.6.

## Histórico: política sentinel24-1.4

Medições após implementar a referência individual de frequência, com 200 solicitações e concorrência oito por execução.

| Cenário | Histórico por pagador | Duração (s) | Válidas/s | p50 (ms) | p95 (ms) | p99 (ms) | Máximo (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mesmo pagador | 0 | 7,30 | 27,41 | 262,04 | 442,65 | 505,85 | 522,50 |
| Pagadores diferentes | 100 | 8,02 | 24,93 | 299,95 | 540,81 | 683,46 | 698,48 |

As 400 solicitações produziram HTTP 201 válidos, sem erros reportados e dentro do alvo interno. O cenário do mesmo pagador produziu sete aprovações e 193 recomendações de segundo fator. O cenário com histórico preparou 20.000 registros e produziu 200 aprovações, exigindo perfil de valores maduro e comparação geográfica avaliada.

## Histórico: política sentinel24-1.2

Cada execução enviou 200 solicitações com concorrência oito.

| Execução | Cenário | Histórico por pagador | p95 (ms) | p99 (ms) | Máximo (ms) | Válidas/s |
|---|---|---:|---:|---:|---:|---:|
| 1 | Pagadores diferentes | 0 | 690,26 | 1.943,55 | 1.949,85 | 20,13 |
| 2 | Mesmo pagador | 0 | 423,67 | 452,31 | 456,90 | 26,95 |
| 3 | Mesmo pagador, repetição | 0 | 393,31 | 441,91 | 453,20 | 29,64 |
| 4 | Pagadores diferentes, repetição | 0 | 504,69 | 552,81 | 587,39 | 25,51 |
| 5 | Pagadores diferentes | 12 | 664,21 | 721,38 | 730,06 | 24,62 |
| 6 | Pagadores diferentes | 100 | 950,80 | 1.073,05 | 1.213,89 | 12,07 |

As 1.200 solicitações produziram HTTP 201 válidos, sem erros reportados e dentro do alvo interno. Nos dois ensaios do mesmo pagador, cada execução produziu sete aprovações e 193 recomendações de segundo fator. Os ensaios com histórico prepararam 2.400 e 20.000 registros, respectivamente.

## Reprodução na versão atual

Acúmulo financeiro, exigindo referência madura e a decisão esperada em cada resposta:

```bash
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload accumulated_amount --history 100
```

Esse cenário exige `--history 100` e acrescenta dois registros recentes por titular. `amount_history_checks` informa quantas respostas confirmaram a referência avaliada e madura. A validação também exige os dois sinais de acúmulo, score 35 somente em `velocity` e segundo fator.

Com API e banco em execução, na raiz:

```bash
python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent
python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload same_payer
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent --history 100
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload shared_device --history 100
```

Destinatário compartilhado, com histórico recém-inserido e estatísticas atualizadas:

```bash
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload same_recipient --history 100
```

Destinatário compartilhado, com manutenção explícita antes da medição:

```bash
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload same_recipient --history 100 --vacuum-history
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 4 --workload same_recipient --history 100 --vacuum-history
```

Sem `--vacuum-history`, cenários com histórico executam ANALYZE durante a preparação. Com a opção, o histórico é confirmado e depois recebe VACUUM ANALYZE sem trabalhadores paralelos, fora da transação de inserção e antes da medição HTTP. A opção exige `--history`.

Esses modos representam condições diferentes de manutenção e devem ser identificados nas comparações. Nenhuma dessas manutenções é executada pela API a cada solicitação.

Cada rodada cria novos identificadores e informa a versão da política, as estatísticas atualizadas, a execução opcional de VACUUM e os contadores de avaliação dos perfis. Os comandos atuais não reproduzem exatamente o método e o estado das bases das rodadas antigas.

## Método e limitações

- O benchmark utiliza clientes concorrentes em ciclo fechado: cada cliente envia sua próxima solicitação depois que a anterior termina. Não simula uma taxa externa constante de chegada nem caracteriza toda a fila de produção.
- O tempo é observado pelo cliente HTTP e inclui comunicação e leitura da resposta. Preparação do histórico, ANALYZE e VACUUM ficam fora da medição.
- Os percentis usam nearest rank e consideram todas as requisições medidas, inclusive respostas de erro.
- `approved`, `two_factor_required` e `rejected` são decisões válidas. HTTP 503 e falhas de transporte são erros técnicos e não entram na taxa de decisões válidas.
- O script retorna código diferente de zero quando encontra erros. Ultrapassar o orçamento de latência, isoladamente, não altera esse código: é necessário consultar `valid_within_budget`.
- Os registros preparados são adicionados à base existente: 20.000 nos cenários históricos com 200 titulares e profundidade 100; 20.400 no cenário de acúmulo, que inclui dois registros recentes adicionais por titular. O tamanho total cresce entre rodadas; o número preparado não representa o total armazenado.
- Aquecimento, cache, estatísticas, manutenção, ordem das rodadas, concorrência e carga da máquina podem influenciar os resultados. As comparações não isolam o efeito causal de cada alteração.
- Os históricos são preparados sinteticamente para exercitar consultas e maturidade. Não foram produzidos por dias de tráfego real nem constituem uma base rotulada de fraude.
- A manutenção e os testes de recuperação do banco devem ocorrer separadamente da medição HTTP.
- Os bloqueios preservam a ordem das análises relacionadas, mas impõem contenção quando pedidos compartilham titulares ou dispositivo. A espera pode encerrar com HTTP 503; isso não é uma recomendação antifraude.
- Limites por comando SQL e conexão não constituem um prazo máximo garantido para toda a requisição HTTP.

Os ensaios documentam comportamento observado nesse ambiente. Não demonstram SLA de produção, capacidade máxima, precisão de detecção ou taxa de falsos positivos.
