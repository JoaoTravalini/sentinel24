# Sentinel24 — Grupo 24

**Integrantes:** João Paulo Motta Travalini · Bruno Bianchi Raiado

Motor de análise de risco para solicitações Pix, desenvolvido para o desafio final do Bootcamp QI Tech 2026.

## Como o motor de decisão funciona

O Sentinel24 combina regras gerais, comportamento individual e um score explicável para recomendar `approved`, `two_factor_required` ou `rejected`.

O processamento é síncrono: a API recebe a solicitação, consulta o histórico, calcula a decisão e a persiste antes de responder. O serviço não executa transferências nem realiza a autenticação de segundo fator.

### Estrutura

A aplicação utiliza Python, FastAPI, SQLAlchemy e PostgreSQL, executados com Docker Compose.

- **Resources:** recebem requisições HTTP e definem as respostas.
- **Schemas e normalização:** validam e padronizam documentos, datas, IP e demais campos.
- **Controllers:** coordenam idempotência, contexto, avaliação e persistência.
- **Repositories:** executam consultas e bloqueios no banco.
- **Rules:** calculam sinais e decisões sem consultar diretamente o banco.
- **DTOs:** organizam o contrato de saída.

O contexto inclui frequência, relacionamento entre titulares, dispositivos, perfil recente do pagador e localização anterior disponível.

O histórico é agrupado por documento do titular. Como o contrato não fornece um identificador completo da instituição da conta de origem, esse agrupamento não representa necessariamente uma única conta bancária.

### Idempotência e concorrência

O campo `id` identifica a solicitação Pix:

- Mesmo identificador e mesmo conteúdo normalizado: recupera a análise persistida.
- Mesmo identificador com conteúdo diferente: retorna `409 Conflict`.
- Outro identificador: realiza uma nova análise, mesmo que o valor seja igual.

Um hash SHA-256 do conteúdo normalizado permite comparar representações equivalentes, como documentos com ou sem máscara.

Antes de consultar o histórico e persistir uma nova análise, a aplicação obtém bloqueios transacionais associados ao identificador Pix, aos titulares envolvidos e ao dispositivo. Os bloqueios são adquiridos em ordem determinística.

Assim, solicitações relacionadas não consultam simultaneamente o mesmo histórico ignorando umas às outras. Após os bloqueios, a aplicação verifica novamente se o identificador já existe.

A restrição única sobre `pix_id` acrescenta uma proteção no PostgreSQL. Os bloqueios são liberados no commit ou rollback.

### Persistência e explicação

Cada análise registra identificador público, identificador Pix, valor em centavos, datas, campos utilizados nas consultas, payload recebido, decisão e versão da política.

A resposta inclui motivos, evidências, pontuação por família, parâmetros e resumo do contexto utilizado.

Uma repetição recupera a decisão original, inclusive sua versão de política. Atualizações da política não reescrevem análises anteriores.

## Regras

Política atual: **`sentinel24-1.4`**.

Os pesos e limiares são hipóteses explícitas de política, verificadas com cenários sintéticos. Não representam probabilidades de fraude nem foram calibrados com uma base rotulada de transações reais.

| Regra | Lógica | Parâmetros | Por quê |
|---|---|---|---|
| Frequência em um minuto | Comparar a atividade atual com uma referência histórica ou com a política inicial. | Sem histórico suficiente: 8 solicitações incluindo a atual. Com histórico: maior entre 8 e duas vezes a mediana dos picos diários de um minuto. Atingir o limite gera 35 pontos em `velocity`. | Diferenciar uma rajada de um titular pouco ativo de uma frequência recorrente de outro titular. |
| Frequência em cinco minutos | Avaliar atividade sustentada em uma janela maior. | Sem histórico suficiente: 20 solicitações incluindo a atual. Com histórico: maior entre 20 e duas vezes a mediana dos picos diários de cinco minutos. Atingir o limite gera 45 pontos em `velocity`. | Complementar a janela curta sem presumir a mesma frequência habitual para todos. |
| Valor elevado | Observar exposição financeira absoluta. | A partir de R$ 5.000; 20 pontos em `transaction_value`. | Considerar exposição sem intervir apenas pelo valor. |
| Valor muito elevado | Observar um segundo patamar de exposição. | A partir de R$ 20.000; 30 pontos em `transaction_value`. | Aumentar a cautela para valores maiores. |
| Valor fora do perfil | Comparar o pagamento com a mediana individual. | Perfil maduro, pelo menos R$ 1.000 e pelo menos 5 vezes a mediana; 25 pontos em `transaction_value`. | Registrar uma despesa excepcional sem exigir segundo fator apenas por esse desvio. |
| Conta de origem recente | Observar pouca antiguidade da conta. | Menos de 7 dias; 15 pontos em `access_context`. | Usar antiguidade como contexto complementar. |
| Dispositivo compartilhado | Identificar associação com vários titulares pagadores. | Pelo menos 3 titulares distintos anteriores em uma hora; 30 pontos em `access_context`. | Observar concentração de acessos sem concluir que compartilhamento seja necessariamente fraude. |
| Dispositivo fora do perfil | Dispositivo ausente da amostra recente. | Perfil maduro; 30 pontos em `access_context`. | Identificar mudança no meio de acesso. |
| IP fora do perfil | IP ausente da amostra recente. | Perfil maduro; 10 pontos em `access_context`. | Tratar mudança de rede como evidência fraca. |
| Canal fora do perfil | Canal ausente da amostra recente. | Perfil maduro; 10 pontos em `access_context`. | Considerar mudança de comportamento sem peso elevado isoladamente. |
| Mudança combinada de acesso | Dispositivo novo acompanhado de IP ou canal novo. | Perfil maduro; 35 pontos em `access_context`. | Dar mais relevância à combinação de mudanças. |
| Destino recente | Observar pouca antiguidade da conta destinatária. | Menos de 24 horas; 25 pontos em `counterparty`. Não aplicado entre titulares iguais. | Acrescentar contexto sobre o destinatário. |
| Destinatário fora do perfil | Titular destinatário ausente da amostra recente. | Perfil maduro; 15 pontos em `counterparty`. Não aplicado entre titulares iguais. | Reconhecer um relacionamento novo sem impedir todo primeiro pagamento. |
| Concentração de entradas | Vários pagadores direcionando solicitações ao mesmo destinatário. | Em 5 minutos: pelo menos 5 pagadores distintos anteriores e 8 solicitações incluindo a atual; 25 pontos em `network_flow`. | Observar concentração com peso insuficiente para intervir isoladamente, pois comerciantes também recebem de muitos clientes. |
| Possível repasse rápido | Comparar uma saída com entradas anteriormente aprovadas na análise. | Em 5 minutos: pelo menos 3 pagadores distintos, soma de pelo menos R$ 1.000 e saída de pelo menos 70% dessa soma; 45 pontos em `network_flow`. | Identificar possível concentração e repasse. Aprovação de análise não comprova liquidação. |
| Abertura posterior à transação | Data de abertura posterior à data informada da solicitação. | 35 pontos em `data_quality`. | Sinalizar inconsistência temporal nos dados. |
| Deslocamento geográfico incompatível | Comparar distância e intervalo entre localizações. | Mesmo titular e dispositivo; referência anterior aprovada em até 24 horas; distância mínima de 100 km; desconto de 30 km e acréscimo de 120 segundos; velocidade resultante acima de 1.000 km/h; 35 pontos em `access_context`. | Aplicar tolerâncias antes de sinalizar incompatibilidade. O GPS recebido não é prova confiável de localização. |

### Referência individual de frequência

A referência utiliza análises anteriormente aprovadas do mesmo titular e dispositivo, dentro de uma janela de 14 dias, excluindo as últimas 24 horas.

Para cada relógio, a consulta calcula contagens em janelas móveis de um e cinco minutos e extrai o maior valor de cada dia ativo. A mediana desses picos representa a frequência de pico observada.

A mediana reduz a influência de um único dia extremo. Os picos diários permitem considerar pagamentos em lote sem diluir esse comportamento nas horas sem atividade.

A personalização exige:

- Pelo menos 3 dias ativos.
- Recebimentos observados em pelo menos 3 dias distintos.
- Pelo menos 48 horas entre o primeiro e o último recebimento da referência.

Essas condições são verificadas separadamente para o horário informado da transação e o horário observado pelo servidor. Datas antigas no payload não substituem tempo real de observação.

Exemplo, sem outros sinais:

| Referência histórica de um minuto | Solicitações atuais | Limite efetivo | Resultado da regra |
|---|---:|---:|---|
| Sem histórico suficiente | 8 | 8 | Sinaliza frequência. |
| Picos habituais de 1 | 10 | 8 | Sinaliza frequência. |
| Picos habituais de 10 | 10 | 20 | Não sinaliza frequência. |
| Picos habituais de 8 | 16 | 16 | Sinaliza frequência. |

Um dispositivo novo não herda automaticamente a referência do anterior. A frequência habitual também não anula sinais de valor, destinatário, acesso ou fluxo.

A exclusão das últimas 24 horas reduz a influência imediata da atividade atual sobre sua própria referência. Não elimina a possibilidade de contaminação persistente do histórico.

A janela de 14 dias, a maturidade mínima e o multiplicador 2 são parâmetros iniciais de política. Os testes demonstram comportamento e limites dos cenários definidos, não a escolha estatisticamente ótima desses números.

### Perfil de valores e familiaridade

O perfil considera até 100 análises anteriormente aprovadas nos últimos 30 dias. Os cinco minutos imediatamente anteriores à solicitação ficam fora dessa amostra.

Para ser considerado maduro, exige:

- Pelo menos 12 amostras.
- Atividade em pelo menos 3 dias distintos, considerando datas em UTC.
- Pelo menos 24 horas entre o primeiro e o último recebimento observado na amostra.

Sem esses requisitos, as regras dependentes desse perfil não pontuam. Ausência de histórico não é tratada como fraude.

A amostra é limitada e não representa todo o histórico do cliente. Uma análise aprovada pelo motor não constitui confirmação de legitimidade.

### Despesas excepcionais

Uma pessoa que costuma fazer pagamentos pequenos pode realizar uma despesa maior, como aluguel.

O desvio de valor contribui com 25 pontos: sozinho, fica abaixo do limite para segundo fator, mas permanece registrado na explicação.

Com perfil maduro e sem outros sinais:

| Cenário | Score | Recomendação |
|---|---:|---|
| Histórico de R$ 50 e pagamento de R$ 1.500 para destinatário habitual, com acesso habitual. | 25 | `approved` |
| Mesmo pagamento para destinatário fora do perfil. | 40 | `two_factor_required` |
| Desvio de valor, mudança combinada de acesso e destinatário fora do perfil. | 75 | `rejected` |

Não há reconhecimento específico de aluguel ou recorrência mensal. O contrato não fornece confirmação de conclusão do segundo fator; uma solicitação anterior com `two_factor_required` não é tratada como pagamento confirmado.

### Relógios e geografia

As regras de frequência e fluxo avaliam separadamente o horário informado da transação e o horário observado pelo servidor. Evidências dos dois relógios não duplicam o peso da mesma regra.

A solicitação atual é acrescentada às contagens quando a condição exige sua inclusão. Contagens de titulares distintos anteriores não recebem esse acréscimo automaticamente.

A comparação geográfica utiliza distância de Haversine entre coordenadas, sem depender de cidades específicas ou serviços externos. Sem localização atual ou referência anterior adequada, a regra não pontua.

## Como as regras viram decisão

As regras pertencem a seis famílias:

| Família | Contexto observado |
|---|---|
| `velocity` | Frequência das solicitações. |
| `transaction_value` | Valor absoluto e desvio em relação ao perfil. |
| `access_context` | Conta de origem, dispositivo, rede, canal e deslocamento. |
| `counterparty` | Antiguidade e relacionamento com o destinatário. |
| `network_flow` | Concentração de entradas e possível repasse. |
| `data_quality` | Inconsistência temporal dos dados. |

Dentro de cada família, somente o **maior peso** contribui para o score. Isso reduz a soma de sinais correlacionados.

Os maiores pesos de cada família são somados, com limite de 100 pontos.

A decisão segue esta ordem:

1. **`rejected`:** score de pelo menos 70, sinais em pelo menos duas famílias e contribuição de pelo menos 30 pontos em `velocity`, `access_context` ou `network_flow`.
2. **`two_factor_required`:** score de pelo menos 35, quando os critérios de rejeição não foram atendidos.
3. **`approved`:** demais casos.

O segundo fator é uma recomendação intermediária diante de risco ou incerteza. O motor não executa essa autenticação.

### Contrato HTTP

As duas rotas Pix exigem o cabeçalho `INTERNAL-TOKEN`.

| Método | Caminho | Resultado |
|---|---|---|
| `POST` | `/pix/transaction` | Analisa e persiste uma nova solicitação, ou recupera sua análise idempotente. |
| `GET` | `/pix/transaction/{id}` | Consulta pelo `id` original do Pix. |
| `GET` | `/health_check` | Verifica se a API responde, sem consultar o banco. |

A resposta de sucesso possui exatamente três campos no primeiro nível:

```json
{
  "key": "UUID da análise",
  "status": "approved",
  "metadata": {
    "policy_version": "sentinel24-1.4",
    "risk_score": 0
  }
}
```

O conteúdo de `metadata` acima está abreviado. A resposta real inclui sinais, parâmetros e contexto, incluindo os limites efetivos de frequência.

| Status HTTP | Situação |
|---|---|
| `201` | Nova análise persistida, inclusive quando a recomendação é `rejected`. |
| `200` | Consulta ou repetição idempotente de análise existente. |
| `400` | Payload ou parâmetro inválido. |
| `403` | Token interno ausente ou incorreto. |
| `404` | Análise não encontrada. |
| `409` | Mesmo identificador Pix com conteúdo diferente. |
| `503` | Falha temporária de banco ou contenção que impediu concluir a análise. |

Uma indisponibilidade técnica não é convertida em aprovação nem em rejeição por fraude. Após a recuperação, o cliente pode repetir o mesmo identificador e conteúdo: se houve persistência, recupera a decisão; caso contrário, uma nova análise pode ser realizada.

## Como rodar e testar

### Pré-requisitos

- Docker com Docker Compose.
- Python 3.11 para executar testes e benchmarks na máquina.

### Subir a aplicação

Na raiz do repositório:

```bash
docker compose up -d --build --wait
docker compose ps
curl -i http://127.0.0.1:3000/health_check
```

O health check deve retornar `204 No Content`.

Os valores padrão permitem executar sem criar um `.env`. As tabelas são criadas automaticamente na inicialização de um banco novo.

Para personalizar, copie `.env.example` para `.env`. Se alterar `DB_PORT`, ajuste também a porta da `DATABASE_URL` usada pelas ferramentas executadas fora do Docker.

O token padrão local é `default_token`, destinado à execução do desafio. Em outro ambiente, deve ser substituído.

### Preparar os testes

```bash
python -m venv .venv
```

No Windows com Git Bash:

```bash
source .venv/Scripts/activate
```

No Linux ou macOS:

```bash
source .venv/bin/activate
```

Instale as dependências:

```bash
python -m pip install -r requirements-dev.txt
```

### Executar os testes

Testes unitários, sem necessidade de subir o banco:

```bash
python -m pytest -q tests/unit
```

Testes unitários e de integração, com API e banco em execução:

```bash
python -m pytest -q tests/unit tests/integration
```

A suíte unitária/de integração possui 138 testes.

Teste E2E de recuperação:

```bash
python -m pytest -v tests/e2e/test_database_recovery.py
```

O E2E interrompe temporariamente o banco do projeto e o inicia novamente. Execute-o separadamente dos demais testes e benchmarks.

A cobertura inclui validação, normalização, idempotência, concorrência, composição de sinais, perfil, despesas excepcionais, frequência adaptativa, limites temporais, geografia, contenção de bloqueios e recuperação do banco.

Os testes de frequência também verificam exclusão da atividade recente, datas retroativas, dispositivo diferente e preservação dos demais sinais de risco.

### Experimentar a API

O exemplo utiliza a configuração padrão:

```bash
curl -i -X POST http://127.0.0.1:3000/pix/transaction \
  -H 'INTERNAL-TOKEN: default_token' \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "pix-demo-001",
    "amount": 10000,
    "transaction_date": "2026-10-17T18:00:00Z",
    "source_account": {
      "branch": "0001",
      "account_number": "88213",
      "opening_date": "2025-03-02T10:00:00Z",
      "owner": {
        "document_number": "123.456.789-09"
      }
    },
    "destination_account": {
      "participant": "60701190",
      "branch": "3675",
      "account_number": "10442",
      "opening_date": "2025-10-10T09:00:00Z",
      "owner": {
        "document_number": "987.654.321-00"
      }
    },
    "source": {
      "device_id": "device-demo-001",
      "channel": "app",
      "ip": "198.51.100.23",
      "geolocation": null
    }
  }'
```

Consultar:

```bash
curl -i http://127.0.0.1:3000/pix/transaction/pix-demo-001 \
  -H 'INTERNAL-TOKEN: default_token'
```

Repetir o POST sem alterações retorna `200` e a mesma análise. Alterar o conteúdo mantendo o identificador retorna `409`. Para uma nova análise, utilize outro `id`.

A recomendação depende do histórico existente. Se o identificador já possuir uma análise de política anterior, sua repetição continuará retornando aquela decisão original.

### Desempenho

```bash
python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent
python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload same_payer
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent --history 12
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent --history 100
```

Os cenários com histórico preparam registros sintéticos antes da medição. O benchmark exige perfil de valores maduro e comparação geográfica avaliada nesses cenários.

Na política `1.4`, dois ensaios de 200 solicitações com oito clientes concorrentes produziram 400 respostas válidas, sem erros reportados, todas dentro do alvo experimental de 2.000 ms.

| Cenário | p95 | p99 | Máximo |
|---|---:|---:|---:|
| Mesmo pagador, sem histórico preparado | 442,65 ms | 505,85 ms | 522,50 ms |
| Pagadores diferentes, 100 registros históricos por pagador | 540,81 ms | 683,46 ms | 698,48 ms |

Esses resultados não representam garantia de latência, capacidade máxima ou acurácia antifraude.

Método, resultados anteriores e limitações estão em [docs/performance.md](docs/performance.md).

### Recuperação e limites

A aplicação limita esperas por conexão e execução de comandos no banco. Conexões inválidas são verificadas ao serem retiradas do pool. Esses mecanismos não constituem um prazo máximo garantido para toda a requisição HTTP.

O E2E verifica que uma interrupção controlada do banco preserva análises anteriores, não cria uma decisão durante a indisponibilidade e permite retentativa segura após o retorno.

A solução não possui integração financeira, confirmação de liquidação Pix, execução de segundo fator ou modelo de aprendizado de máquina. Os dados recebidos são contexto para análise, não identidade, localização ou legitimidade comprovadas.

Para interromper os serviços:

```bash
docker compose stop
```