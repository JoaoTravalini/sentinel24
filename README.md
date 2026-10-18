# Sentinel24 — Grupo 24

**Integrantes:** João Paulo Motta Travalini · Bruno Bianchi Raiado

Motor de análise de risco de solicitações Pix, desenvolvido para o desafio final do Bootcamp QI Tech 2026. Política atual: **`sentinel24-1.10`**.

## Como o motor de decisão funciona

O Sentinel24 combina um limite operacional noturno, regras gerais, histórico individual e um score explicável para recomendar `approved`, `two_factor_required` ou `rejected`.

O processamento é síncrono: a API valida a solicitação, consulta o histórico, calcula a decisão e a persiste antes de responder. O serviço não executa transferências nem realiza a autenticação de segundo fator.

### Estrutura e persistência

A aplicação utiliza Python 3.11, FastAPI, SQLAlchemy e PostgreSQL 16, executados com Docker Compose.

- **Resources:** recebem as requisições e definem as respostas HTTP.
- **Schemas e normalização:** validam e padronizam a entrada.
- **Controllers:** coordenam idempotência, análise e transação de banco.
- **Repositories:** consultam o histórico, obtêm bloqueios e persistem análises.
- **Rules:** calculam sinais e decisão sem acessar diretamente o banco.
- **DTOs:** organizam a saída.

Cada nova solicitação válida recebe uma análise persistida, inclusive quando o resultado é segundo fator ou rejeição. A tabela `pix_analyses` registra identificador público, identificador Pix, valor inteiro em centavos, datas, campos de consulta, payload original, decisão e versão da política. Requisições inválidas recebem erro de validação.

O histórico comportamental é agrupado pelo documento do titular. O limite noturno utiliza documento, agência e número da conta de origem. Como o contrato não fornece a instituição da conta de origem, a simulação assume que essas contas pertencem a uma única instituição; não identifica contas iguais em bancos diferentes.

### Idempotência e concorrência

- Mesmo `id` e conteúdo normalizado: retorna a análise original, incluindo sua versão de política.
- Mesmo `id` com conteúdo diferente: retorna `409 Conflict`.
- Outro `id`: realiza nova análise, mesmo que o valor seja igual.

O conteúdo normalizado é comparado por hash SHA-256. Documentos com ou sem máscara e datas equivalentes são padronizados antes dessa comparação.

Antes de consultar o histórico, o motor obtém bloqueios transacionais associados ao Pix, aos titulares envolvidos e ao dispositivo, em ordem determinística. Depois verifica novamente se o Pix já existe. Uma restrição única sobre `pix_id` acrescenta proteção no banco.

Os bloqueios permanecem até commit ou rollback. Assim, solicitações relacionadas não podem consultar simultaneamente o mesmo histórico ignorando as análises umas das outras. O custo é contenção: compartilhar titular ou dispositivo reduz o paralelismo e pode resultar em indisponibilidade temporária.

A decisão é gravada e confirmada na mesma transação utilizada para a análise. Uma alteração posterior da política não reescreve decisões anteriores.

## Regras

Pesos e limiares são hipóteses explícitas de política, verificadas com cenários sintéticos. Não são probabilidades de fraude nem foram calibrados com uma base rotulada de transações reais.

| Regra | Lógica | Parâmetros | Por quê |
|---|---|---|---|
| Frequência em um minuto | Comparar atividade atual com referência individual ou política inicial. | Limite inicial de 8 solicitações incluindo a atual; com histórico maduro, maior entre 8 e duas vezes a mediana dos picos diários de um minuto. Atingir o limite gera 35 pontos em `velocity`. | Identificar rajadas sem impor a mesma frequência a todos. |
| Frequência em cinco minutos | Avaliar atividade sustentada em janela maior. | Limite inicial de 20; com histórico maduro, maior entre 20 e duas vezes a mediana dos picos diários de cinco minutos. Atingir o limite gera 45 pontos em `velocity`. | Complementar a janela curta sem duplicar seu peso. |
| Acúmulo financeiro em uma hora e em 24 horas | Comparar a soma de pagamentos a outros titulares, retirando o maior valor da janela, com a referência individual calculada da mesma forma. | Perfil maduro, pelo menos 3 pagamentos incluindo o atual e soma ajustada que atinja o maior entre R$ 1.000 e duas vezes a mediana dos picos diários ajustados. Cada janela gera 35 pontos em `velocity`. | Identificar pagamentos distribuídos, preservando uma despesa excepcional e adaptando a referência ao volume habitual. |
| Valor elevado | Observar exposição financeira absoluta. | A partir de R$ 5.000; 20 pontos em `transaction_value`. | Acrescentar contexto de exposição sem intervir apenas pelo valor. |
| Valor muito elevado | Observar um segundo patamar de exposição. | A partir de R$ 20.000; 30 pontos em `transaction_value`. | Aumentar a cautela sem bloquear isoladamente uma operação de maior valor. |
| Valor fora do perfil | Comparar com a mediana individual. | Perfil maduro, pelo menos R$ 1.000 e pelo menos cinco vezes a mediana; 25 pontos em `transaction_value`. | Registrar despesa excepcional sem exigir segundo fator apenas pelo desvio. |
| Conta de origem recente | Observar antiguidade da conta. | Menos de 7 dias; 15 pontos em `access_context`. | Usar pouca antiguidade como contexto, sem impedir o início de uso. |
| Compartilhamento não estabelecido de dispositivo | Contar titulares recentes sem relacionamento histórico com o aparelho. | Pelo menos 3 titulares pagadores distintos anteriores em uma hora sem relacionamento estabelecido; 30 pontos em `access_context`. | Distinguir compartilhamento recorrente de novas associações concentradas. |
| Dispositivo fora do perfil | Identificar aparelho ausente da amostra do pagador. | Perfil maduro; 30 pontos em `access_context`. | Considerar a mudança de aparelho sem usá-la, isoladamente, para sustentar rejeição. |
| IP fora do perfil | Identificar rede ausente da amostra. | Perfil maduro; 10 pontos em `access_context`. | Mudanças de rede podem ser legítimas e recebem peso baixo. |
| Canal fora do perfil | Identificar canal ausente da amostra. | Perfil maduro; 10 pontos em `access_context`. | Tratar uma mudança isolada como contexto fraco. |
| Mudança combinada de acesso | Dispositivo novo acompanhado de IP ou canal novo. | Perfil maduro; 35 pontos em `access_context`. | Exigir mais cautela diante de mudanças combinadas. Não comprova comprometimento. |
| Destino recente | Observar antiguidade da conta destinatária. | Menos de 24 horas; 25 pontos em `counterparty`. Não aplicado entre titulares iguais. | Acrescentar contexto sobre contas recém-abertas. |
| Destinatário fora do perfil | Identificar titular destinatário ausente da amostra. | Perfil maduro; 15 pontos em `counterparty`. Não aplicado entre titulares iguais. | Reconhecer relacionamento novo sem impedir todo primeiro pagamento. |
| Concentração de entradas | Comparar recebimentos com o histórico do destinatário. | Em 5 minutos: pelo menos 5 pagadores distintos anteriores e contagem incluindo a atual que atinja o limite efetivo. Inicial: 8; com perfil maduro: maior entre 8 e duas vezes a mediana dos picos diários. 25 pontos em `network_flow`. Não aplicado entre titulares iguais. | Distinguir concentração incomum de um destinatário habitualmente movimentado. |
| Possível repasse rápido | Comparar saída com entradas anteriormente aprovadas na análise. | Em 5 minutos: pelo menos 3 pagadores distintos, soma de pelo menos R$ 1.000 e saída de pelo menos 70% dessa soma; 45 pontos em `network_flow`. Não aplicado entre titulares iguais. | Sinalizar possível concentração e repasse. Aprovação de análise não comprova liquidação. |
| Abertura posterior à transação | Comparar datas de abertura e transação. | Abertura posterior à transação; 35 pontos em `data_quality`. | Pedir confirmação diante de inconsistência temporal. |
| Deslocamento geográfico incompatível | Comparar distância e tempo entre localizações do mesmo titular e aparelho. | Referência anterior aprovada em até 24 horas. Sinaliza quando a distância excede `30 + 1000 × (segundos decorridos + 120) / 3600`, em km; 35 pontos em `access_context`. | Usar uma margem contínua, sem um corte adicional de 100 km. Coordenadas declaradas não sustentam rejeição por si. |

### Histórico e adaptação a diferentes perfis

| Referência | Histórico elegível | Maturidade mínima |
|---|---|---|
| Valores, dispositivo, IP, canal e destinatário do pagador | Até 100 análises aprovadas nos últimos 30 dias pelo horário da transação, excluindo os 5 minutos imediatamente anteriores; somente registros já observados pelo servidor. | 12 amostras, 3 dias de transação distintos e 24 horas entre a primeira e a última observação da amostra. |
| Frequência do pagador | Mesmo titular e dispositivo; análises aprovadas em janela de 14 dias, excluindo as últimas 24 horas. | Por relógio: 3 dias ativos, 3 dias observados e intervalo observado de pelo menos 48 horas. |
| Acúmulo financeiro do pagador | Mesmo titular, reunindo contas e dispositivos; análises aprovadas para outros titulares, pelo horário observado, em 14 dias, excluindo as últimas 24 horas. | 12 amostras, 3 dias observados distintos e intervalo de pelo menos 48 horas. |
| Relacionamento com dispositivo | Mesmo titular e dispositivo; análises aprovadas em janela de 14 dias, excluindo as últimas 24 horas, pelo horário observado. | 3 dias observados distintos e intervalo de pelo menos 48 horas. |
| Recebimentos do destinatário | Mesmo titular destinatário, reunindo diferentes pagadores e dispositivos; análises aprovadas em janela de 14 dias, excluindo as últimas 24 horas. | Por relógio: 3 dias ativos, 3 dias observados e intervalo observado de pelo menos 48 horas. |

Dias são calculados em UTC. Datas antigas no payload não substituem tempo real de observação. Sem maturidade, o perfil do pagador não gera sinais de novidade ou desvio de valor individual e o acúmulo não gera sinal personalizado; frequência e concentração utilizam os limites iniciais.

Frequência e concentração utilizam a mediana dos maiores volumes observados em janelas móveis de cada dia ativo. Isso considera pagamentos em lote sem diluí-los nas horas sem atividade e reduz a influência de um único dia extremo. O multiplicador de dois admite crescimento em relação à referência; não foi estimado como limite estatístico de fraude.

Por exemplo, um pagador com pico mediano de 10 solicitações por minuto recebe limite de 20. Um destinatário com pico mediano de 12 recebimentos em cinco minutos recebe limite de 24. Atingir o limite ainda sinaliza risco; familiaridade não anula outras famílias.

Um dispositivo novo não herda o limite personalizado do anterior. No compartilhamento de aparelho, somente os titulares recentes sem relacionamento histórico estabelecido entram na contagem de três. Os relacionamentos expiram com a janela histórica.

A exclusão de 24 horas dificulta que a rajada atual aumente imediatamente sua própria referência. Análises rejeitadas ou com segundo fator não estabelecem familiaridade. Isso não elimina contaminação persistente: uma análise aprovada não é confirmação externa de legitimidade.

### Acúmulo financeiro e despesas excepcionais

A regra considera análises anteriores aprovadas e acrescenta a solicitação atual. Transferências entre contas do mesmo titular ficam fora tanto da atividade quanto da referência desse sinal. Contas e dispositivos diferentes do mesmo titular continuam compondo o volume de saída.

Para cada janela móvel, calcula `soma dos valores - maior valor`. A referência utiliza a mediana dos maiores totais ajustados de cada dia ativo. A mesma transformação é aplicada ao histórico e ao pedido atual.

Por exemplo, R$ 1.500 + R$ 20 + R$ 30 deixam R$ 50 após o ajuste: uma despesa grande isolada não caracteriza acúmulo. Já três pagamentos de R$ 900 deixam R$ 1.800 e podem sinalizar desvio se atingirem a referência individual. Um usuário habituado a volumes maiores pode ter limiar acima desse valor.

Frequência e volume acumulado compartilham `velocity`: o mesmo aumento de atividade pode elevar ambos, e somá-los automaticamente contaria evidências relacionadas duas vezes. As janelas também não somam seus pesos entre si. O acúmulo, sozinho, pede segundo fator e não é sinal elegível de apoio à rejeição.

A consulta histórica só é executada se houver pelo menos três pagamentos e soma ajustada de pelo menos R$ 1.000 em alguma janela. A consulta lê mais 24 horas antes do início da referência para calcular corretamente as janelas na fronteira. Esses registros adicionais não contam para amostras, dias ou intervalo de maturidade.

Retirar o maior pagamento é uma tolerância explícita, não um detector de aluguel. Ela pode reduzir sensibilidade a perdas concentradas em uma operação; os sinais de valor individual e as demais famílias continuam ativos. Sem histórico maduro, não se presume uma referência pessoal de acúmulo.

### Limite operacional noturno

O limite noturno é uma condição operacional separada do score. No escopo implementado:

- Aplica-se a solicitações de pessoa física para pessoa física, identificadas pelo formato do documento normalizado.
- O padrão é R$ 1.000 acumulados por conta de origem, das 20h às 6h, em UTC-03:00.
- O relógio utilizado é o observado pelo banco de dados após os bloqueios, não a data enviada no payload.
- A janela começa no horário configurado e termina às 6h; não reinicia à meia-noite. O início é inclusivo e o fim exclusivo.
- Somam-se os valores integrais das análises anteriores aprovadas da conta para destinatários PF no período. Análises rejeitadas ou com segundo fator não consomem essa simulação de limite.
- Somar exatamente o limite é permitido; ultrapassá-lo resulta em `rejected`, mesmo com score zero. Segundo fator não substitui esse limite.
- `ACCOUNT_NIGHT_POLICIES`, em `src/rules/night_limit.py`, permite configurações previamente autorizadas pela instituição: limite específico e início às 20h ou 22h. Essa configuração não vem do payload e não há endpoint para solicitar alterações.

O histórico usado pelo limite e a nova análise ficam protegidos pelos bloqueios do titular até o commit, evitando aprovações concorrentes acima do limite simulado. Repetições idempotentes recuperam a decisão original, sem novo consumo.

A contabilização utiliza aprovações do motor, não pagamentos liquidados. O serviço não recebe confirmação de liquidação, cancelamento ou conclusão de segundo fator e não representa uma implementação completa de controles regulatórios. Operações fora do escopo PF para PF continuam sujeitas às regras de risco.

### Racional dos parâmetros e dos pesos

As janelas de um e cinco minutos separam rajadas curtas de atividade sustentada. A janela de uma hora observa compartilhamento de aparelho; as de 14 e 30 dias equilibram referência recente e quantidade de observações. Amostras, dias e intervalos mínimos evitam personalização imediata com poucos registros. Os valores exatos permanecem escolhas iniciais de política.

Os patamares de R$ 5.000 e R$ 20.000 representam exposição absoluta; a comparação com cinco vezes a mediana acrescenta contexto individual. O piso de R$ 1.000 evita sinalizar multiplicações de valores muito pequenos. Nenhum desses sinais de valor individual, isoladamente, alcança os 35 pontos para segundo fator. O acúmulo tem critério próprio, depende de referência madura e contribui para `velocity`.

As idades de 7 dias e 24 horas acrescentam contexto de antiguidade. Três titulares no aparelho e cinco pagadores no destinatário representam multiplicidade, sem afirmar que compartilhamento ou volume sejam fraude. A condição de repasse combina diversidade, materialidade e proporção da saída; também pode ocorrer em atividades legítimas.

Os pesos distinguem contexto fraco, exposição e alterações comportamentais. Foram confrontados com cenários de despesas excepcionais, mudanças de acesso, compartilhamento, volume habitual e crescimento anormal. Esses testes demonstram o comportamento definido; não medem precisão ou falsos positivos em clientes reais.

### Relógios e geografia

Frequência e fluxo avaliam separadamente o horário informado da transação e o horário observado pelo servidor, obtido no banco após adquirir os bloqueios. Evidências de relógios diferentes não são misturadas para completar uma condição nem duplicam o peso da mesma regra.

Acúmulo financeiro e limite noturno utilizam somente o horário observado. A data declarada não altera suas janelas.

A solicitação atual é acrescentada às contagens quando a condição exige sua inclusão. Quantidades de titulares distintos anteriores não recebem esse acréscimo automaticamente.

A geografia usa Haversine, sem depender de cidades específicas ou serviços externos. Sem coordenadas atuais ou referência anterior adequada, não pontua. A referência deve ser do mesmo titular e aparelho e ter horário de transação anterior ou igual ao atual.

A margem de 30 km, o acréscimo de 120 segundos e a velocidade de referência de 1.000 km/h são tolerâncias operacionais, não precisão medida do GPS ou limites aprendidos. O contrato não fornece precisão nem horário de aquisição da localização. Em vez de aprender maior tolerância a partir das próprias aprovações, o motor mantém essa política explícita.

No mesmo instante, a distância permitida pela fórmula é de aproximadamente 63,33 km: um salto de 80 km sinaliza incompatibilidade. Em uma hora, 400 km ficam dentro da margem. Os testes verificam fronteiras e sensibilidade a parâmetros alternativos, sem estabelecer calibração ótima.

## Como as regras viram decisão

| Família | Contexto observado |
|---|---|
| `velocity` | Frequência e volume financeiro acumulado. |
| `transaction_value` | Valor absoluto e desvio em relação ao perfil. |
| `access_context` | Conta de origem, dispositivo, rede, canal e deslocamento. |
| `counterparty` | Antiguidade e relacionamento com o destinatário. |
| `network_flow` | Concentração de entradas e possível repasse. |
| `data_quality` | Inconsistência temporal dos dados. |

Somente o maior peso de cada família contribui para o score. Os máximos são somados, com limite de 100 pontos. Isso reduz dupla contagem de sinais relacionados, sem pressupor independência estatística entre famílias.

Se o limite noturno aplicável foi excedido, a recomendação final é `rejected`. A resposta identifica `decision_basis: "operational_limit"`, `operational_reason_codes: ["NIGHT_LIMIT_EXCEEDED"]` e o cálculo em `operational_checks.night_limit`. Esse motivo não acrescenta pontos ao score.

Nos demais casos, a decisão de risco segue esta ordem:

1. **`rejected`:** score de pelo menos 70, sinais em pelo menos duas famílias e pelo menos um sinal elegível de apoio à rejeição com peso de 30 ou mais.
2. **`two_factor_required`:** score de pelo menos 35, quando os critérios de rejeição não foram atendidos.
3. **`approved`:** demais casos.

Os sinais elegíveis de apoio à rejeição são:

- `SOURCE_BURST_1M` e `SOURCE_BURST_5M`.
- `SHARED_DEVICE`, para compartilhamento sem relacionamento estabelecido.
- `ACCESS_CONTEXT_CHANGE`, para mudanças combinadas de acesso.
- `RAPID_FORWARDING_PATTERN`, para possível repasse rápido.

Geografia, dispositivo novo isolado e acúmulo financeiro continuam contribuindo para o score, mas não satisfazem esse requisito de apoio. Assim, score alto não determina rejeição automaticamente. A resposta registra os códigos elegíveis que efetivamente dispararam em `rejection_support_codes`.

Os 35 pontos permitem segundo fator por um sinal mais relevante ou combinação de sinais fracos. Os 70 pontos e os critérios adicionais tornam a rejeição mais restritiva. Esses critérios são decisões de política, não provas de fraude.

### Exemplos verificados

Os exemplos de risco consideram o limite operacional não excedido e os sinais descritos; referências maduras são utilizadas nas regras que dependem de perfil.

| Cenário | Score | Recomendação |
|---|---:|---|
| Histórico de R$ 50 e pagamento de R$ 1.500 para destinatário e acesso habituais. | 25 | `approved` |
| Mesmo pagamento para destinatário fora do perfil. | 40 | `two_factor_required` |
| Geografia incompatível (35), valor elevado (20) e conta destinatária recém-aberta (25). | 80 | `two_factor_required` |
| R$ 20.000 para destinatário novo, em celular novo, com IP e canal conhecidos. | 75 | `two_factor_required` |
| Mesmo cenário, com mudança também de IP. | 80 | `rejected` |
| Geografia incompatível e frequência acima do limite de um minuto, sem outros sinais. | 70 | `rejected` |
| Acúmulo acima da referência nas duas janelas, sem outros sinais. | 35 | `two_factor_required` |
| Frequência de um minuto e acúmulo acima da referência, sem outros sinais. | 35 | `two_factor_required` |
| Acúmulo acima da referência e mudança combinada de dispositivo e IP. | 70 | `rejected` |
| Limite noturno excedido, sem sinais de risco. | 0 | `rejected` por limite operacional |

Não há reconhecimento específico de aluguel nem execução de segundo fator. O contrato não informa sua conclusão; uma análise com `two_factor_required` não vira pagamento confirmado ou familiaridade automaticamente.

### Contrato HTTP

As duas rotas Pix exigem `INTERNAL-TOKEN`.

| Método | Caminho | Resultado |
|---|---|---|
| `POST` | `/pix/transaction` | Analisa e persiste ou recupera a análise idempotente. |
| `GET` | `/pix/transaction/{id}` | Consulta pelo identificador original do Pix. |
| `GET` | `/health_check` | Retorna `204`, sem consultar o banco. |

A resposta de sucesso possui exatamente três campos no primeiro nível:

```json
{
  "key": "UUID da análise",
  "status": "approved",
  "metadata": {
    "policy_version": "sentinel24-1.10",
    "risk_score": 0,
    "decision_basis": "risk_policy",
    "operational_reason_codes": [],
    "rejection_support_codes": []
  }
}
```

`metadata` está abreviado no exemplo. A resposta real inclui sinais, evidências, famílias, parâmetros e resumos do contexto; não exporta os registros individuais do perfil nem as coordenadas utilizadas na comparação geográfica.

| HTTP | Situação |
|---|---|
| `201` | Nova análise persistida, inclusive segundo fator ou rejeição. |
| `200` | Consulta ou repetição idempotente. |
| `400` | Payload ou parâmetro inválido. |
| `403` | Token interno ausente ou incorreto. |
| `404` | Análise não encontrada. |
| `409` | Mesmo Pix com conteúdo diferente. |
| `503` | Falha temporária de banco ou contenção que impediu concluir. |

Indisponibilidade técnica não vira aprovação ou rejeição por fraude. Após a recuperação, repetir o mesmo identificador e conteúdo recupera a análise, caso tenha sido persistida, ou permite uma nova tentativa de análise.

## Como rodar e testar

### Subir a aplicação

Requisitos: Docker com Docker Compose; Python 3.11 para testes e benchmarks na máquina.

Na raiz:

```bash
docker compose up -d --build --wait
docker compose ps
curl -i http://127.0.0.1:3000/health_check
```

O health check deve retornar `204 No Content`. Um banco novo recebe automaticamente as tabelas e os índices de `database/database.sql`.

Não é necessário criar `.env` para usar os padrões locais. Para personalizar, copie `.env.example` para `.env`. Se alterar `DB_PORT`, ajuste também a porta da `DATABASE_URL` usada pelas ferramentas fora do Docker. O token local padrão é `default_token`, destinado à execução do desafio.

### Preparar e executar os testes

```bash
python -m venv .venv
```

Windows com Git Bash:

```bash
source .venv/Scripts/activate
```

Linux ou macOS:

```bash
source .venv/bin/activate
```

Instale e execute, com API e banco disponíveis:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q tests/unit tests/integration
```

A suíte contém **193 testes unitários e 101 de integração: 294 no total**, além de um E2E executado separadamente. Abrange validação, normalização, idempotência, concorrência, perfis, frequência, dispositivos, destinatários, geografia, limite noturno, acúmulo financeiro, composição da decisão e contenção. Os 294 testes e o E2E de recuperação passaram na validação da versão 1.10.

O E2E de recuperação é executado separadamente:

```bash
python -m pytest -v tests/e2e/test_database_recovery.py
```

Ele interrompe e reinicia o banco do projeto. Verifica preservação de análises, erro técnico durante a indisponibilidade e retentativa segura após a recuperação. Execute-o separadamente dos demais testes e benchmarks.

### Experimentar a API

Com a configuração padrão:

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
      "owner": {"document_number": "123.456.789-09"}
    },
    "destination_account": {
      "participant": "60701190",
      "branch": "3675",
      "account_number": "10442",
      "opening_date": "2025-10-10T09:00:00Z",
      "owner": {"document_number": "987.654.321-00"}
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

Repetir o POST retorna `200` e a mesma análise. Alterar o conteúdo mantendo o identificador retorna `409`. Para nova análise, utilize outro `id`. A recomendação depende do histórico existente.

### Desempenho e limites

Os ensaios da política **`sentinel24-1.10`** utilizaram `accumulated_amount`, com um e oito clientes concorrentes. Cada execução enviou 200 solicitações de titulares independentes e preparou 20.400 registros sintéticos antes da medição: 100 históricos e dois pagamentos recentes por titular.

Em cada execução, as 200 respostas confirmaram referência de acúmulo avaliada e madura, score 35 somente em `velocity`, sinais de uma hora e 24 horas e recomendação `two_factor_required`.

| Medida | Concorrência 1 | Concorrência 8 |
|---|---:|---:|
| HTTP 201 válidos | 200 | 200 |
| Erros | 0 | 0 |
| Decisões válidas em até 2.000 ms | 200 | 200 |
| Duração HTTP | 17,11 s | 12,31 s |
| Decisões válidas/s | 11,69 | 16,24 |
| p50 | 71,75 ms | 425,85 ms |
| p95 | 131,07 ms | 883,21 ms |
| **p99** | **203,81 ms** | **1.572,60 ms** |
| Máximo | 971,37 ms | 1.609,77 ms |

Os tempos representam a requisição HTTP completa observada pelo cliente. São duas execuções locais em ciclo fechado; cache, carga da máquina, ordem e crescimento da base também podem influenciar os resultados. Com 200 observações por execução, a amostra da cauda de latência é limitada.

A preparação executou ANALYZE, sem VACUUM explícito. Dois segundos são um alvo experimental interno, não um SLA ou prazo oficial de liquidação Pix.

Os ensaios históricos da política 1.6 para o mesmo destinatário continuam documentados: com concorrência oito ocorreram três HTTP 503 em 200 solicitações; com quatro, nenhuma. O novo cenário utiliza titulares, destinatários e dispositivos diferentes e não demonstra solução daquela contenção.

Para medir a versão em execução:

```bash
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 1 --workload accumulated_amount --history 100
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload accumulated_amount --history 100
python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload independent
python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload same_payer
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 8 --workload shared_device --history 100
PYTHONPATH=src python scripts/benchmark_pix.py --requests 200 --concurrency 4 --workload same_recipient --history 100 --vacuum-history
```

Os cenários com histórico inserem dados sintéticos e executam ANALYZE antes da medição HTTP. `--vacuum-history` executa VACUUM ANALYZE sem trabalhadores paralelos, após confirmar a inserção. Essa manutenção fica fora do tempo medido e não é executada pela API a cada solicitação.

A base cresce entre rodadas. O benchmark utiliza clientes em ciclo fechado; não simula uma taxa externa constante de chegada. Método, demais resultados e limitações estão em [docs/performance.md](docs/performance.md).

Limites de espera por conexão e comando SQL não constituem prazo máximo para toda a requisição HTTP. O health check indica que a API responde; não é uma verificação de disponibilidade do banco.

O serviço não possui integração financeira, confirmação de liquidação Pix, execução de segundo fator ou modelo de aprendizado de máquina. Os dados recebidos são contexto para análise, não identidade, localização ou legitimidade comprovadas.

A organização dos arquivos está em [docs/como-o-projeto-e-organizado.md](docs/como-o-projeto-e-organizado.md).

Para interromper os serviços e continuar depois:

```bash
docker compose stop
docker compose start
```