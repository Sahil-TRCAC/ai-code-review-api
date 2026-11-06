# AI Code Review API

A Django REST API that accepts source code, reviews it with Groq in the background, and stores the score and findings. A signed GitHub push can start the same review after the repository is linked to a user.

This is a fresher portfolio project. Local end-to-end review was tested with SQLite, Redis, Celery, and Groq. It is not a production system.

## Project overview

A signed-in user posts code. Django checks the request, saves a review as `pending`, and puts a job on Redis. A Celery worker calls Groq, then saves the summary, score, bugs, security issues, and quality suggestions. The user reads the finished review through the API.

## Problem statement

A first pass for bugs, risky patterns, and simple quality notes takes time. This API keeps that pass off the HTTP request so the client gets a quick acceptance response, then polls for the stored result.

## Key features

- Email and password registration, with Django's password hashing.
- JWT access and refresh tokens.
- Code submission and review lookup limited to the signed-in user.
- Review statuses: `pending`, `processing`, `completed`, `failed`.
- Background processing with Celery and Redis.
- Groq chat completions as the default model provider. Anthropic and OpenAI exist in code and are unused unless `LLM_PROVIDER` is changed.
- Daily review limit from `DAILY_REVIEW_LIMIT`.
- GitHub push webhook with HMAC SHA-256 checks, a unique delivery id, and a repository-to-user link.
- Staff-only stats endpoint.

## Technology stack

| Piece | Role in this repo |
| --- | --- |
| Python, Django, Django REST Framework | API, ORM, admin, validation |
| SimpleJWT | Login tokens |
| SQLite | Database used for the verified local run |
| PostgreSQL settings | `DATABASE_URL` can point at Postgres. That path was not verified here |
| Celery | Worker process for the LLM call and webhook diff download |
| Redis | Celery broker and result backend |
| Groq | Model API used in the successful local review |
| Docker | Optional way to start Redis. Docker itself is not required to read the code |

`render.yaml` is a Render config file in the repo. This pass did not deploy it, and that file is not proof the service is online.

## Architecture overview

```mermaid
flowchart LR
    client[Client or GitHub]
    api[Django API]
    db[(SQLite or PostgreSQL)]
    redis[(Redis)]
    worker[Celery worker]
    groq[Groq API]

    client --> api
    api --> db
    api --> redis
    redis --> worker
    worker --> groq
    worker --> db
    client --> api
```

## How the application works

Manual review:

```text
User submits code
  -> Django validates the JWT, daily limit, code, and language
  -> review row is saved as pending
  -> Celery task is queued through Redis after the database commit
  -> worker calls Groq
  -> results are saved
  -> user retrieves the completed review
```

The submit call returns HTTP 202. The review is not finished yet. Poll `GET /api/review/<id>/` until `status` is `completed` or `failed`.

The LLM runs outside the request because Groq can take longer than a normal API call and can fail. Celery retries `process_review` once. Redis only carries the task message. The review text lives in the database.

GitHub is a separate path, described below. It does not replace the manual submit flow.

## Project folder structure

Commands below assume this folder is the one that contains `manage.py`.

```text
manage.py
config/                 Django settings, URLs, Celery app
apps/users/             Registration, profile, daily limit
apps/reviews/           Submit, list, detail, LLM task
apps/webhooks/          GitHub signature check and diff task
tests.py                Auth and review tests
tests_webhooks.py       Webhook tests
requirements.txt
.env.example            Placeholders only
render.yaml             Unverified Render config
PROJECT_EXPLANATION.md  Longer interview notes
```

## Installation and setup on Windows

Use PowerShell. Install Python 3.11 or newer, then:

```powershell
cd path\to\ai-code-review-api-main
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` in an editor. Do not commit it.

If `Activate.ps1` is blocked, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` and try again.

## Environment variable configuration

Names below are the ones `config/settings.py` reads. Values in `.env.example` are placeholders.

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Django signing key |
| `DEBUG` | `True` on your machine |
| `ALLOWED_HOSTS` | Comma-separated hosts |
| `DATABASE_URL` | Database URL |
| `REDIS_URL` | Used when a Celery URL is omitted |
| `CELERY_BROKER_URL` | Redis URL for the queue |
| `CELERY_RESULT_BACKEND` | Redis URL for task results |
| `CELERY_BROKER_TRANSPORT` | `redis` |
| `CELERY_TASK_ALWAYS_EAGER` | Keep `false` so work stays on the worker |
| `CELERY_TASK_EAGER_PROPAGATES` | Keep `false` with eager mode off |
| `LLM_PROVIDER` | `groq`, `anthropic`, or `openai` |
| `GROQ_API_KEY` | Your Groq key |
| `GROQ_MODEL` | A model id your Groq account can call |
| `ANTHROPIC_API_KEY` | Only if provider is `anthropic` |
| `OPENAI_API_KEY` | Only if provider is `openai` |
| `JWT_ACCESS_TOKEN_LIFETIME` | Access token lifetime in minutes |
| `JWT_REFRESH_TOKEN_LIFETIME` | Refresh token lifetime in minutes |
| `DAILY_REVIEW_LIMIT` | Reviews per user per reset window |
| `GITHUB_WEBHOOK_SECRET` | Same secret as the GitHub webhook |
| `CORS_ALLOWED_ORIGINS` | Comma-separated browser origins |

`CELERY_WORKER_POOL` is not read by Django. On Windows, pass `--pool=solo` to the worker command.

## Database configuration

The verified local run used SQLite:

```text
DATABASE_URL=sqlite:///db.sqlite3
```

PostgreSQL is supported by `dj-database-url` and the default in `settings.py` when `DATABASE_URL` is missing:

```text
DATABASE_URL=postgres://user:password@localhost:5432/codereview
```

That Postgres URL was not verified against a working server in this project. Create the database yourself before `migrate` if you switch to Postgres.

From the `manage.py` folder, after `.env` points at a database you can log into:

```powershell
python manage.py showmigrations
python manage.py migrate
```

Do not point `DATABASE_URL` at a shared or production database for a first try. `db.sqlite3` is gitignored.

## Running Django locally

```powershell
.\venv\Scripts\Activate.ps1
python manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:8000/ . A healthy process returns JSON with `"status": "ok"`.

Create an admin user when you want Django admin:

```powershell
python manage.py createsuperuser
```

Admin is http://127.0.0.1:8000/admin/ .

## Starting Redis using Docker

Install Docker Desktop, then:

```powershell
docker run -d --name codereview-redis -p 6379:6379 redis:7
```

If the container already exists:

```powershell
docker start codereview-redis
```

The broker URL in `.env` should stay `redis://localhost:6379/0`.

## Starting the Celery worker

Open a second PowerShell window in the same folder, activate the virtualenv, then:

```powershell
python -m celery -A config worker --loglevel=info --pool=solo
```

`--pool=solo` is required on Windows. The worker must be started from the folder that contains `manage.py` and the `config` package. If Celery says the module `config` was not found, you are in the parent folder.

Leave this window open. Submit a review only after the log says the worker is ready. Restart the worker after you change `.env`, because it reads settings at startup.

## Configuring Groq

Set these in `.env`:

```text
LLM_PROVIDER=groq
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=openai/gpt-oss-20b
```

`llama-3.3-70b-versatile` was rejected by Groq for the account used in local testing. `openai/gpt-oss-20b` succeeded. If Groq returns `model_not_found`, pick an id from `GET https://api.groq.com/openai/v1/models` and put it in `GROQ_MODEL`, then restart the worker.

A real local review was submitted, processed by Celery, and returned with status `completed`. That test used SQLite.

## API endpoints and example requests

In PowerShell, call `curl.exe`. `curl` alone is an alias and does not accept these flags.

### Register

`POST /api/auth/register/`

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/auth/register/ -H "Content-Type: application/json" -d "{\"email\":\"you@email.com\",\"username\":\"you\",\"password\":\"Pass1234!\",\"password_confirm\":\"Pass1234!\"}"
```

### Login

`POST /api/auth/login/`

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/auth/login/ -H "Content-Type: application/json" -d "{\"email\":\"you@email.com\",\"password\":\"Pass1234!\"}"
```

Copy `access` from the response.

### Refresh

`POST /api/auth/refresh/` with `{"refresh":"<refresh-token>"}` returns a new access token. The same refresh token stays valid until it expires. Refresh-token blacklisting is off because `rest_framework_simplejwt.token_blacklist` is not installed.

### Profile

`GET /api/auth/profile/` and `PATCH /api/auth/profile/` require `Authorization: Bearer <access>`.

`PATCH` can set `github_token`. The response does not include that field. The token is stored as plaintext. It is not encrypted.

### Submit code

`POST /api/review/`

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/review/ -H "Authorization: Bearer PASTE_ACCESS_TOKEN" -H "Content-Type: application/json" -d "{\"code\":\"def divide(a, b):\n    return a / b\",\"language\":\"python\"}"
```

Success looks like:

```json
{"id": 1, "status": "pending", "message": "Review submitted. Processing in background."}
```

### Read one review

`GET /api/review/<id>/`

```powershell
curl.exe http://127.0.0.1:8000/api/review/1/ -H "Authorization: Bearer PASTE_ACCESS_TOKEN"
```

Another user's id returns 404.

### List reviews

`GET /api/reviews/` supports `language`, `status`, `ordering` (`created_at` or `score`), `date_from`, and `date_to`.

### Staff stats

`GET /api/admin/stats/` returns 403 unless `is_staff` is true.

### Health

`GET /` returns the status JSON. It does not require a token.

## Example AI code review response

The shape below matches `ReviewSerializer`. The wording is an illustration, not a copy of a stored row.

```json
{
  "id": 1,
  "language": "python",
  "status": "completed",
  "summary": "The function divides without checking for zero.",
  "score": 70,
  "bugs": [
    {"line": 2, "issue": "ZeroDivisionError when b is 0", "severity": "high"}
  ],
  "security_issues": [],
  "quality_suggestions": [
    {"suggestion": "Handle b == 0 before dividing", "severity": "medium"}
  ],
  "created_at": "2026-11-06T13:30:00Z",
  "updated_at": "2026-11-06T13:30:05Z"
}
```

`raw_llm_response` is stored on the review but is not part of this API response.

## Running automated tests

```powershell
.\venv\Scripts\Activate.ps1
python -m pytest -q
```

Tests use SQLite in memory and mock Groq and GitHub. They do not prove a live webhook delivery.

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
```

## GitHub webhook setup

This webhook path is implemented. A real GitHub delivery was not sent during verification.

1. Set `GITHUB_WEBHOOK_SECRET` in `.env` and restart Django.
2. Create a superuser and open `/admin/`.
3. Add a `Tracked repository` whose full name is `owner/repo`, linked to the user who should own the review.
4. For a private repository, `PATCH /api/auth/profile/` with that user's access token and `{"github_token":"your_github_token"}`.
5. In GitHub, add a webhook:
   - Payload URL: `https://<your-host>/webhooks/github/`
   - Content type: `application/json`
   - Secret: the same value as `GITHUB_WEBHOOK_SECRET`
   - Event: Just the push event

What the code does:

```text
GitHub push
  -> Django checks X-Hub-Signature-256 over the raw body
  -> unknown or ambiguous repository is rejected
  -> X-GitHub-Delivery is stored once
  -> HTTP 202
  -> Celery downloads the diff from GitHub
  -> a Review is created and process_review runs the same Groq path
```

A missing secret, a missing signature, or a bad signature returns 403. The header `X-GitHub-Token` is ignored.

Local GitHub cannot reach `127.0.0.1` unless you use a tunnel. That tunnel was not part of the verified test.

## Current limitations

- PostgreSQL configuration exists and was not verified.
- No production deployment was verified.
- GitHub tokens are plaintext in the database.
- Refresh tokens are not revoked on use.
- A real GitHub webhook delivery was not verified.
- Reviews stay `pending` if Redis or the worker is down.
- Groq model ids change. A model that worked once can later return `model_not_found`.
- Large GitHub comparisons can be marked partial.
- `apps/webhooks/serializers.py` is not used by any route.
- This is not enterprise security and it is not production-ready.

## Future improvements

- Verify `migrate` against a Postgres database you control.
- Encrypt `github_token` with a key that stays in the environment, using a deliberate data migration.
- Install the SimpleJWT blacklist app if refresh tokens must be revoked.
- Send one real push webhook through a tunnel and record the result.
- Add a small page for linking a repository instead of using the admin only.

## Troubleshooting

| What you see | What to check |
| --- | --- |
| `config` module was not found | The shell is not in the folder that contains `manage.py` |
| Review stays `pending` | Redis is running and the Celery window says `ready` |
| `Invalid API Key` in the worker log | `GROQ_API_KEY` in `.env`, then restart the worker |
| `model_not_found` | Change `GROQ_MODEL`, then restart the worker |
| `database table is locked` | Another process has `db.sqlite3` open. Stop the extra server |
| Port 8000 already in use | Another `runserver` is still running |
| Webhook returns 403 | Secret in GitHub matches `GITHUB_WEBHOOK_SECRET`, and the header is `sha256=` |
| Webhook returns 404 | `Tracked repository` full name matches `owner/repo` |
