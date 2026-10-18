# Sentinel24 — Grupo 24

**Integrantes:** João Paulo Motta Travalini · Bruno Bianchi Raiado

Motor de análise de risco para solicitações Pix, desenvolvido para o desafio final do Bootcamp QI Tech 2026.

## Como o motor de decisão funciona

O Sentinel24 recebe uma solicitação Pix, consulta o histórico disponível, avalia sinais de risco e devolve uma recomendação: `approved`, `two_factor_required` ou `rejected`.

O processamento é síncrono: a decisão é devolvida na própria resposta do POST, depois de persistida no PostgreSQL. O serviço recomenda uma ação; não executa transferências nem realiza a autenticação de segundo fator.

### Estrutura

A aplicação utiliza Python, FastAPI, SQLAlchemy e PostgreSQL, executados com Docker Compose.

- **Resources:** recebem as requisições HTTP e definem as respostas.
- **Schemas e normalização:** validam o formato e padronizam documentos, datas, IP e demais campos.
- **Controllers:** coordenam idempotência, consulta do contexto, avaliação e persistência.
- **Repositories:** executam as consultas e os bloqueios no banco.
- **Rules:** calculam os sinais e a decisão, sem realizar consultas ao banco.
- **DTOs:** organizam o contrato de saída.

O contexto histórico inclui frequência de solicitações, relacionamento entre titulares, uso de dispositivos, perfil recente do pagador e localização anterior disponível.

As análises são agrupadas por documento do titular. O contrato não fornece um identificador completo da instituição da conta de origem; portanto, esse histórico não representa necessariamente uma única conta bancária.

### Idempotência e concorrência

O campo `id` identifica a solicitação Pix:

- Mesmo `id` e mesmo conteúdo normalizado: retorna a análise persistida, sem recalcular nem inserir outro registro.
- Mesmo `id` com conteúdo diferente: retorna `409 Conflict`.
- Outro `id`: executa uma nova análise, mesmo que o valor seja igual.

O conteúdo normalizado recebe um hash SHA-256. A comparação considera equivalências de representação, como documentos com ou sem máscara.

Antes de consultar o histórico e inserir uma nova análise, a aplicação obtém bloqueios transacionais associados ao identificador Pix, aos titulares envolvidos e ao dispositivo. Os bloqueios são adquiridos em ordem determinística.

Isso impede que solicitações relacionadas consultem simultaneamente o mesmo histórico e tomem decisões ignorando umas às outras. Depois de adquirir os bloqueios, a aplicação verifica novamente a existência do `id`.

O PostgreSQL mantém uma restrição única sobre `pix_id` como proteção adicional. Os bloqueios são liberados no commit ou rollback.

### Persistência e explicação

Cada análise registra identificador público, identificador Pix, valor em centavos, datas, campos utilizados nas consultas, payload recebido, decisão e versão da política.

A resposta contém os motivos, evidências, pontuação por família, parâmetros e um resumo do contexto utilizado. Repetir uma solicitação recupera a decisão original, inclusive sua versão de política.

## Regras

Política atual: **`sentinel24-1.2`**.

Os pesos são heurísticos e representam força relativa dos sinais. Não são probabilidades de fraude e não foram calibrados com uma base rotulada de transações reais.

| Regra | Lógica | Parâmetros | Por quê |
|---|---|---|---|
| Frequência em um minuto | Muitas solicitações do mesmo titular em uma janela curta. | Pelo menos 8 solicitações, incluindo a atual; 35 pontos em `velocity`. | Identificar rajadas sem depender de valores elevados. |
| Frequência em cinco minutos | Volume persistente de solicitações do mesmo titular. | Pelo menos 20 solicitações, incluindo a atual; 45 pontos em `velocity`. | Complementar a janela curta e observar atividade sustentada. |
| Valor elevado | Valor absoluto acima do primeiro patamar. | A partir de R$ 5.000; 20 pontos em `transaction_value`. | Considerar a exposição financeira sem rejeitar apenas pelo valor. |
| Valor muito elevado | Valor absoluto acima do segundo patamar. | A partir de R$ 20.000; 30 pontos em `transaction_value`. | Aumentar a cautela para maior exposição financeira. |
| Valor fora do perfil | Valor muito superior à mediana do perfil recente. | Perfil maduro, valor de pelo menos R$ 1.000 e pelo menos 5 vezes a mediana; 35 pontos em `transaction_value`. | Comparar o valor com o comportamento observado do titular. |
| Conta de origem recente | Pouco tempo entre abertura e solicitação. | Menos de 7 dias; 15 pontos em `access_context`. | Tratar a pouca antiguidade como sinal complementar, sem bloquear isoladamente novos clientes. |
| Dispositivo compartilhado | Dispositivo associado a vários titulares pagadores. | Pelo menos 3 titulares distintos anteriores em uma hora; 30 pontos em `access_context`. | Observar possível concentração de acessos sem concluir que compartilhamento seja necessariamente fraude. |
| Dispositivo fora do perfil | Dispositivo ausente da amostra recente do titular. | Perfil maduro; 30 pontos em `access_context`. | Identificar mudança relevante no meio de acesso. |
| IP fora do perfil | IP ausente da amostra recente. | Perfil maduro; 10 pontos em `access_context`. | Usar mudança de rede como evidência fraca, pois pode ocorrer legitimamente. |
| Canal fora do perfil | Canal ausente da amostra recente. | Perfil maduro; 10 pontos em `access_context`. | Observar mudança de comportamento sem dar peso elevado ao canal isolado. |
| Mudança combinada de acesso | Dispositivo novo acompanhado de IP ou canal novo. | Perfil maduro; 35 pontos em `access_context`. | Dar mais relevância à combinação do que às mudanças fracas isoladas. |
| Destino recente | Conta de destino aberta recentemente. | Menos de 24 horas; 25 pontos em `counterparty`. Não aplicado entre titulares iguais. | Considerar destinatários de pouca antiguidade como contexto adicional. |
| Destinatário fora do perfil | Titular destinatário ausente da amostra recente. | Perfil maduro; 15 pontos em `counterparty`. Não aplicado entre titulares iguais. | Reconhecer novidade no relacionamento sem impedir novos pagamentos legítimos. |
| Concentração de entradas | Vários pagadores direcionando solicitações ao mesmo destinatário. | Em 5 minutos: pelo menos 5 pagadores distintos anteriores e 8 solicitações incluindo a atual; 25 pontos em `network_flow`. | Observar concentração, mantendo peso insuficiente para exigir segundo fator isoladamente, pois comerciantes também recebem de muitos clientes. |
| Possível repasse rápido | Solicitação de saída elevada em relação a entradas anteriormente aprovadas na análise. | Em 5 minutos: pelo menos 3 pagadores distintos, soma de pelo menos R$ 1.000 e saída de pelo menos 70% dessa soma; 45 pontos em `network_flow`. | Identificar um possível padrão de concentração e repasse. Aprovação de análise não comprova liquidação financeira. |
| Abertura posterior à transação | Data de abertura de uma das contas posterior à data informada da solicitação. | 35 pontos em `data_quality`. | Sinalizar inconsistência temporal nos dados recebidos. |
| Deslocamento geográfico incompatível | Distância e intervalo entre localizações sugerem deslocamento improvável. | Mesmo titular e dispositivo; referência anterior aprovada em até 24 horas; distância mínima de 100 km; desconto de 30 km e acréscimo de 120 segundos; velocidade resultante acima de 1.000 km/h; 35 pontos em `access_context`. | Usar uma margem de tolerância antes de sinalizar incompatibilidade. Não presume que o GPS recebido seja uma prova confiável de localização. |

### Construção do perfil

O perfil considera até 100 análises anteriormente aprovadas nos últimos 30 dias. Os cinco minutos imediatamente anteriores à solicitação ficam fora dessa amostra, reduzindo a influência de uma rajada sobre o próprio perfil.

Para ser considerado maduro, o perfil precisa reunir:

- Pelo menos 12 amostras.
- Atividade em pelo menos 3 dias distintos, considerando datas em UTC.
- Pelo menos 24 horas entre o primeiro e o último recebimento observado na amostra.

Sem esses requisitos, as regras dependentes de perfil não pontuam. A ausência de histórico não é tratada como fraude.

A amostra é limitada e não representa todo o histórico do cliente. Análises aprovadas também não constituem confirmação de legitimidade.

### Janelas temporais e geografia

As regras de frequência e fluxo avaliam separadamente o horário informado da transação e o horário observado pelo servidor. Evidências dos dois relógios não duplicam o peso da mesma regra.

A solicitação atual ainda não está persistida durante a avaliação. Ela é acrescentada às contagens quando a condição exige sua inclusão; contagens de titulares distintos anteriores não recebem esse acréscimo automaticamente.

A comparação geográfica utiliza distância de Haversine entre coordenadas, sem depender de cidades específicas ou de serviços externos. Sem localização atual ou referência anterior adequada, a regra não pontua.

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

Dentro de cada família, somente o **maior peso** contribui para o score. Isso reduz a soma de sinais correlacionados, como dispositivo novo e mudança combinada de acesso.

Os maiores pesos de cada família são somados, com limite de 100 pontos.

A decisão segue esta ordem:

1. **`rejected`:** score de pelo menos 70, sinais em pelo menos duas famílias e contribuição de pelo menos 30 pontos em `velocity`, `access_context` ou `network_flow`.
2. **`two_factor_required`:** score de pelo menos 35, quando os critérios de rejeição não foram atendidos.
3. **`approved`:** demais casos.

Valor elevado e conta de destino recente, por exemplo, não bastam isoladamente para rejeitar uma solicitação. O segundo fator funciona como recomendação intermediária diante de risco ou incerteza.

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
    "policy_version": "sentinel24-1.2",
    "risk_score": 0
  }
}
```

O conteúdo de `metadata` acima está abreviado. A resposta real inclui os sinais e o contexto da decisão.

| Status HTTP | Situação |
|---|---|
| `201` | Nova análise persistida, inclusive quando a recomendação é `rejected`. |
| `200` | Consulta ou repetição idempotente de uma análise existente. |
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

Os valores padrão permitem executar sem criar um `.env`. A criação das tabelas ocorre automaticamente na inicialização de um banco novo.

Para personalizar a configuração, copie `.env.example` para `.env`. Se alterar `DB_PORT`, ajuste também a porta da `DATABASE_URL` usada pelas ferramentas executadas fora do Docker.

O token padrão local é `default_token`. Ele é destinado à execução do desafio e deve ser substituído em outro ambiente.

### Preparar o ambiente de testes

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

Teste E2E de recuperação após indisponibilidade:

```bash
python -m pytest -v tests/e2e/test_database_recovery.py
```

O E2E interrompe temporariamente o banco do projeto e o inicia novamente. Execute-o separadamente dos demais testes e benchmarks.

Na verificação registrada em 17/10/2026, passaram 108 testes unitários/de integração e um teste E2E.

A cobertura inclui validação, normalização, idempotência, concorrência, composição das regras, limites temporais, perfil, geografia, contenção de bloqueios e recuperação do banco.

### Experimentar a API

O exemplo abaixo utiliza os valores padrão de configuração:

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

Consultar a análise:

```bash
curl -i http://127.0.0.1:3000/pix/transaction/pix-demo-001 \
  -H 'INTERNAL-TOKEN: default_token'
```

Repetir o POST sem alterações retorna `200` e a mesma análise. Alterar o conteúdo mantendo o identificador retorna `409`. Para solicitar uma nova análise, utilize outro `id`.

A recomendação pode variar conforme o histórico já existente na base.

### Desempenho

```bash
python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent
python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload same_payer
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent --history 12
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent --history 100
```

Os cenários com histórico preparam registros sintéticos antes da medição e verificam se o perfil maduro e a comparação geográfica foram avaliados.

Nos seis ensaios locais registrados, 1.200 solicitações retornaram decisões válidas dentro do alvo interno de 2.000 ms, sem erros reportados. Esse alvo é experimental: não representa garantia de latência nem capacidade de produção.

Método, resultados completos e limitações estão em [docs/performance.md](docs/performance.md).

### Recuperação e limites

A aplicação limita esperas por conexão e por execução de comandos no banco. Conexões inválidas são verificadas ao serem retiradas do pool. Esses mecanismos não constituem um prazo máximo garantido para toda a requisição HTTP.

O teste de recuperação verifica que uma interrupção controlada do banco preserva análises anteriores, não cria uma decisão durante a indisponibilidade e permite retentativa segura após o retorno.

A solução não possui integração com instituições financeiras, confirmação de liquidação Pix, execução de segundo fator ou modelo de aprendizado de máquina. Os dados recebidos são contexto para análise, não identidade, localização ou legitimidade comprovadas.

Para interromper os serviços:

```bash
docker compose stop
```