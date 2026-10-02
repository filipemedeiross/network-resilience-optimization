# Network Resilience Optimization

Aplicação web Django para experimentar problemas de resiliência em redes. Os
cenários e as partidas ficam em SQLite; a otimização MIP roda em um serviço
FastAPI/CBC isolado.

```text
Navegador ──HTTP──> Django/Gunicorn ──> SQLite
                         │
        comando de publicação ──HTTP──> FastAPI + Python-MIP/CBC
```

O solver não é chamado a cada jogada. O operador resolve uma revisão uma vez,
o Django verifica e persiste o ótimo, e as tentativas dos jogadores são
avaliadas localmente. Não há contas ou login: cada partida pertence a um UUID
guardado na sessão anônima do navegador.

## Caminho recomendado: tudo com Docker Compose

Pré-requisitos: Docker Engine ou Docker Desktop com Compose v2. No WSL 2,
habilite a integração da distribuição nas configurações do Docker Desktop.

1. Prepare a configuração local:

   ```bash
   cp .env.example .env
   python -c "import secrets; print(secrets.token_urlsafe(50))"
   ```

   Copie o valor gerado para `DJANGO_SECRET_KEY` no `.env`.

2. Construa e inicie o Django e o solver:

   ```bash
   docker compose up --build -d
   docker compose ps
   ```

   O entrypoint do contêiner web executa `migrate`, configura o SQLite em WAL
   e coleta os arquivos estáticos automaticamente.

3. Cadastre e resolva os cenários conhecidos:

   ```bash
   docker compose exec web python manage.py seed_scenarios
   docker compose exec web python manage.py solve_scenarios --time-limit 10
   ```

   A carga padrão mantém seis instâncias determinísticas de cada cenário. Para
   cada sessão anônima, o início de partidas usa todas as instâncias publicadas
   antes de repetir a menos recente. A tela do jogo oferece a ação **Nova
   instância**, que cria outra partida sem apagar a anterior.

4. Abra <http://127.0.0.1:8000>. O readiness check do Django fica em
   <http://127.0.0.1:8000/health/ready/>. O solver só é acessível na rede
   interna do Compose.

Comandos úteis:

```bash
docker compose logs -f web solver
docker compose exec web python manage.py migrate
docker compose down
```

`docker compose down` mantém o volume `game_data`. Usar `down -v` também apaga
o banco e deve ser feito apenas quando essa perda for intencional.

## Desenvolvimento: Django local e solver em contêiner

Este modo dá recarga rápida ao Django e mantém CBC isolado.

1. Crie o ambiente Python e instale a aplicação web:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -r requirements/web.txt
   ```

2. Inicie apenas o solver, publicado somente em `127.0.0.1:8001`:

   ```bash
   docker compose -f compose.yaml -f compose.solver-local.yaml up --build solver
   ```

3. Em outro terminal, prepare o Django e o banco:

   ```bash
   source .venv/bin/activate
   export DJANGO_DEBUG=true
   export DJANGO_SECRET_KEY=development-only-change-me
   export SOLVER_SERVICE_URL=http://127.0.0.1:8001

   python manage.py migrate
   python manage.py configure_sqlite
   python manage.py seed_scenarios
   python manage.py solve_scenarios --time-limit 10
   python manage.py runserver
   ```

Se `SOLVER_PORT` for alterado no Compose, ajuste também
`SOLVER_SERVICE_URL` no terminal local.

## Alternativa: solver também local

Para trabalhar sem Docker, instale as dependências do CBC/Python-MIP e inicie
o serviço em outro terminal:

```bash
source .venv/bin/activate
python -m pip install -r requirements/solver.txt
uvicorn solver_service.main:app --host 127.0.0.1 --port 8001
```

Depois execute o mesmo fluxo local de migração, carga, resolução e `runserver`
da seção anterior.

## Banco, cenários e publicação

As entidades principais são:

- `Scenario` e `ScenarioRevision`: cenário lógico e conteúdo versionado;
- `SolverResult`: resultado, bound, gap, hash e verificação independente;
- `Play`: partida anônima vinculada ao gabarito usado ao iniciá-la;
- `Attempt`: tentativa append-only com idempotência e versão otimista.

`seed_scenarios` é idempotente e cria, por padrão, seis instâncias distintas
por tipo. Para ampliar o pool para as oito primeiras sementes:

```bash
python manage.py seed_scenarios --instances-per-kind 8
python manage.py solve_scenarios --time-limit 10
```

Em um banco criado antes da existência do pool, execute novamente os dois
comandos acima: a carga adiciona apenas os conteúdos ausentes e o segundo
comando publica seus gabaritos. O jogo nunca chama o MIP no clique de início ou
restart; ele usa somente instâncias já resolvidas e verificadas, evitando que a
requisição web fique presa à duração do solver.

O parâmetro `--new-revision` existe para versionamento/auditoria, mas duplica o
mesmo conteúdo e não deve ser usado para criar variedade de tabuleiros.

Para recalcular revisões já publicadas:

```bash
python manage.py solve_scenarios --force --time-limit 10
```

O gabarito anterior continua disponível durante e após uma re-resolução que
falhe. Reservas abandonadas pelo solver são recuperadas depois do TTL definido
por `SOLVER_CLAIM_TTL_SECONDS`.

As migrações ficam em `games/migrations/`. Ao alterar modelos:

```bash
python manage.py makemigrations
python manage.py migrate
```

Para remover sessões anônimas expiradas periodicamente:

```bash
python manage.py clearsessions
```

## Testes

Instale os dois conjuntos de dependências de desenvolvimento e rode:

```bash
python -m pip install -r requirements/dev.txt -r requirements/solver-dev.txt
pytest -q
python manage.py check --database default
python manage.py makemigrations --check --dry-run
```

## Configuração e limites

As opções documentadas estão em `.env.example`. As principais são:

- `WEB_BIND` e `WEB_PORT`: endereço/porta expostos pelo Compose;
- `DJANGO_ALLOWED_HOSTS`: hosts aceitos, separados por vírgula;
- `DJANGO_SECURE_COOKIES`: use `true` somente quando o acesso for HTTPS;
- `SOLVER_TIME_LIMIT`: limite MIP solicitado pelo Django;
- `SOLVER_MAX_TIME_SECONDS`: teto aceito pelo serviço;
- `SOLVER_MAX_CONCURRENCY`: quantidade de resoluções simultâneas;
- `SOLVER_CLAIM_TTL_SECONDS`: recuperação de resoluções interrompidas.

O Compose se vincula a `127.0.0.1` por padrão. Como não existe autenticação,
não exponha o app diretamente à internet. Para acesso público, coloque um proxy
HTTPS com rate limiting na frente e troque o segredo, hosts e cookies seguros.

SQLite com WAL e transações curtas é adequado para esta aplicação pequena em
um único host. Se houver múltiplas réplicas web ou muitas escritas simultâneas,
migre para PostgreSQL; aumentar o timeout apenas adia erros de lock. Veja as
[notas oficiais do Django sobre SQLite](https://docs.djangoproject.com/en/5.2/ref/databases/#sqlite-notes),
a [rede de serviços do Compose](https://docs.docker.com/compose/how-tos/networking/)
e o [limite de tempo do Python-MIP](https://python-mip.readthedocs.io/en/latest/quickstart.html).

## Estrutura nova

```text
config/                    configuração e rotas Django
games/                     domínio, views, templates, estáticos e comandos
solver_service/            API FastAPI e formulações MIP
requirements/              dependências web, solver e testes
docker/                    entrypoint web
compose.yaml               pilha completa
compose.solver-local.yaml  override para desenvolvimento híbrido
```
