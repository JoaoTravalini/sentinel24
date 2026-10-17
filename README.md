# Sentinel24

**Grupo 24 — Bootcamp QI Tech 2026**

API antifraude para análise de solicitações Pix, desenvolvida a partir do Base Service do bootcamp.

## Estado atual

Base preparada para iniciar a implementação do motor antifraude:

- Aplicação FastAPI e PostgreSQL executados com Docker Compose.
- Middlewares de contexto, logs, autenticação interna e gerenciamento de sessão preservados.
- Entidade de exemplo e integração de boletos removidas.
- Teste de integração HTTP para verificar a disponibilidade da API.

As regras antifraude e os endpoints Pix serão implementados nas próximas etapas.

## Como executar

Requisito: Docker com Docker Compose.

```bash
docker compose up -d --build --wait
```

Verifique a API:

```bash
curl -i http://127.0.0.1:3000/health_check
```

Resposta esperada: `204 No Content`.

As configurações padrão permitem executar sem criar um arquivo `.env`. Para personalizá-las, consulte `.env.example`.

## Como testar

Com Python 3.11 e um ambiente virtual ativo, instale as dependências:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Com os contêineres em execução:

```bash
python -m pytest -q
```

## Como consultar os logs

```bash
docker compose logs --tail=100 api
```

## Como encerrar

```bash
docker compose stop
```