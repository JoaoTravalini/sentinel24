# Organização do Sentinel24

O projeto parte do Base Service e mantém suas camadas. O motor Pix é síncrono e a política atual está em `src/rules/engine.py`.

## Caminho de uma análise

1. `src/app.py` registra `POST /pix/transaction` e `GET /pix/transaction/{pix_id:path}`.
2. Os middlewares preparam identificador da requisição e logs, verificam `INTERNAL-TOKEN` e gerenciam o contexto da sessão.
3. `src/resources/pix_analysis.py` valida o POST com `src/schemas/post_pix_transaction.json`.
4. `src/controllers/pix_analysis_controller.py` normaliza a entrada, verifica idempotência, obtém bloqueios e coordena a análise.
5. `src/repositories/risk_context_repository.py` consulta atividade e referências anteriores. A estrutura SQL de atividade fica em `src/repositories/risk_activity_query.py`.
6. `src/rules/engine.py` combina os sinais dos módulos de regras e escolhe a recomendação sem consultar o banco.
7. `src/repositories/pix_analysis_repository.py` persiste. O controller prepara o DTO e confirma a transação antes de responder.
8. O middleware fecha a sessão e limpa o contexto, inclusive quando ocorre erro.

Uma repetição idempotente recupera a decisão persistida e não recalcula as regras. O GET também recupera a análise gravada.

## Onde alterar

| Necessidade | Arquivos principais |
|---|---|
| Contrato de entrada | `src/schemas/post_pix_transaction.json` e `src/utils/pix_payload.py` |
| Rotas e status HTTP | `src/app.py` e `src/resources/pix_analysis.py` |
| Coordenação, idempotência e transação | `src/controllers/pix_analysis_controller.py` |
| Bloqueios, criação e busca de análise | `src/repositories/pix_analysis_repository.py` |
| Consultas de histórico | `src/repositories/risk_context_repository.py` e `src/repositories/risk_activity_query.py` |
| Sinais, pesos e decisão final | `src/rules/engine.py` e módulos de `src/rules/` |
| Resposta JSON | `src/dtos/pix_analysis_dto.py` |
| Modelagem persistida | `database/database.sql` e `src/models/pix_analysis.py` |
| Erros da análise | `src/errors/pix_analysis_errors.py` |
| Conexão, sessão e contexto | `src/database.py`, `src/controllers/base_controller.py` e `src/middlewares/session_manager.py` |

## Context e Session

O middleware cria um `Context` por requisição. Ele começa sem sessão. Ao construir um controller, o `BaseController` obtém o contexto e cria a sessão SQLAlchemy quando necessário.

Os repositories recebem esse mesmo contexto e utilizam `context.db_session`. A sessão mantém a transação; o contexto é o objeto que permite compartilhar essa sessão entre as camadas da requisição.

O controller decide commit ou rollback. O middleware garante o fechamento da sessão. O health check não constrói um controller e não consulta o banco.

## Regras e testes

- `profile.py`: valor relativo e familiaridade de acesso e destinatário.
- `velocity.py`: frequência com referência individual.
- `accumulation.py`: volume ajustado em uma hora e 24 horas, com referência individual.
- `night_limit.py`: período noturno e limite operacional configurável por conta.
- `device.py`: compartilhamento sem relacionamento estabelecido.
- `recipient.py`: concentração de recebimentos com referência do destinatário.
- `geography.py`: consistência entre distância e tempo declarados.
- `engine.py`: regras complementares, famílias, pesos e critérios de decisão.

As famílias atravessam módulos: `access_context`, por exemplo, recebe sinais de perfil, dispositivo e geografia. Módulo não equivale a família. Frequência e acúmulo compartilham `velocity` e contribuem com o maior peso, evitando somar evidências relacionadas. O limite noturno é uma condição operacional: pode rejeitar com score zero e aparece em `operational_checks`, separado dos sinais de risco.

Os testes unitários exercitam normalização e decisões com contextos controlados. Os testes de integração validam HTTP e consultas no PostgreSQL. O E2E de recuperação interrompe e reinicia o banco, por isso deve ser executado separadamente.

Ao mudar o comportamento da política, atualize sua versão, os testes afetados e o README. Preserve a versão e a decisão das análises já gravadas. Medições antigas de desempenho devem continuar identificadas pela política em que foram feitas.
