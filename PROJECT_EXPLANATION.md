# Project explanation

This note is for learning and interviews. It describes the code in this repository. It does not describe a production system, and it does not claim that a live GitHub repository was connected during development.

## 1. Overall architecture

The project is one Django project named `config`, with three apps:

- `apps.users` registers people and stores a daily review counter and an optional GitHub token.
- `apps.reviews` accepts code, queues a Celery task, calls an LLM, and stores the findings.
- `apps.webhooks` accepts GitHub push events and turns a diff into a normal review.

PostgreSQL is the database when the app runs for real (`DATABASE_URL`). Redis carries Celery messages. The HTTP process does not wait for the LLM.

```text
HTTP request
  -> validate and save a row
  -> commit the database transaction
  -> publish a Celery message to Redis
  -> return 202

Celery worker
  -> read the message from Redis
  -> call Groq/OpenAI/Anthropic or the GitHub API
  -> update PostgreSQL
```

Django admin is available at `/admin/` for staff users. That is where a `TrackedRepository` row is created. There is no separate "connect GitHub" API.

## 2. What happens when a user submits code

`POST /api/review/` is handled by `SubmitReviewView` in `apps/reviews/views.py`.

1. DRF requires a valid JWT because the view sets `permission_classes = [IsAuthenticated]`.
2. `_check_rate_limit` increments the user's daily counter. If the user is already at `DAILY_REVIEW_LIMIT`, the API returns 429 and does not save a review.
3. `ReviewCreateSerializer` accepts only `code` and `language`.
4. Inside `transaction.atomic()`, the view saves a `Review` with status `pending`.
5. `transaction.on_commit` queues `process_review.delay(review_id)` only after that row is committed, then stores the Celery task id.
6. The response is HTTP 202 with the review id and status `pending`.

The client later calls `GET /api/review/<id>/`. The queryset is `Review.objects.filter(user=request.user)`, so one user cannot read another user's review.

## 3. How Django receives and validates the request

`manage.py` and `config/wsgi.py` start Django. `config/urls.py` routes URLs to the apps.

DRF settings in `config/settings.py` set:

- authentication class: `JWTAuthentication`
- default permission: `IsAuthenticated`
- page size: 20

A serializer is the validation step. `ReviewCreateSerializer` is a `ModelSerializer` limited to `code` and `language`. Invalid JSON or missing fields return 400 before a review is saved. The webhook view does its own checks: HMAC signature, JSON parsing, repository name shape, and delivery id.

Passwords are never stored raw. `User` extends Django's `AbstractUser`, and registration calls `create_user`, which hashes the password.

## 4. How Celery and Redis work together

Celery is the worker library. Redis is the queue.

`config/celery.py` builds a Celery app and loads every Django setting whose name starts with `CELERY_`. `config/__init__.py` imports that app so tasks use the configured broker.

`CELERY_BROKER_URL` defaults to `REDIS_URL`. When the view calls `process_review.delay(review_id)`, Celery publishes a message. A separate process, `celery -A config worker`, reads it and runs the function.

`CELERY_TASK_ALWAYS_EAGER` is false unless the environment sets it. Eager mode would run the task inside the web request, which hides bugs and makes the request wait. Leave it false and run a worker.

`on_commit` matters because a worker can be fast. If the message were sent before the database commit, the worker might not find the new row.

## 5. How the AI model analyses the code

`process_review` in `apps/reviews/tasks.py` loads the review, sets `processing`, and calls `LLMService.review_code`.

`apps/reviews/llm_service.py` reads `LLM_PROVIDER`. The default path is Groq's chat completions API. Anthropic and OpenAI are the other branches. The system prompt tells the model to return only a JSON object with `summary`, `bugs`, `security`, `quality`, and `score`.

The service strips optional Markdown fences and `json.loads` the text. If the provider returns non-JSON, the task treats that as a failure.

The model does not see the database. It only sees the code string and the language name.

## 6. How the result is saved in PostgreSQL

`Review` stores `summary`, `score`, `raw_llm_response`, and `status`. Related tables hold the list items:

- `Bug`: line, issue, severity
- `SecurityIssue`: line, issue, severity
- `QualitySuggestion`: suggestion, severity

Those rows are replaced inside `transaction.atomic()` when a review completes, so a failed save does not leave half of a new result committed by itself. The review status becomes `completed`.

If the exception happens in a Celery worker and `max_retries` is still available, the status stays non-failed and Celery retries once. When no retry remains, the status is `failed`.

## 7. How GitHub webhooks trigger a review

`GitHubWebhookView` in `apps/webhooks/views.py` is public (`AllowAny`) because GitHub does not send a JWT. The shared secret is the check.

1. Reject the request when `GITHUB_WEBHOOK_SECRET` is empty, the signature header is missing, the value does not start with `sha256=`, or `hmac.compare_digest` fails. The digest is HMAC-SHA256 of the raw body.
2. Ignore any `X-GitHub-Event` other than `push`.
3. Read added, modified, and removed files from `commits` or `head_commit`. If there are no file names and the before/after SHAs do not describe a real change, return 200 and do nothing.
4. Resolve `repository.full_name` through `TrackedRepository`. The match is case-insensitive. The unique index on `LOWER(full_name)` is there so `Test/Repo` and `test/repo` cannot point at two users. Ambiguous rows return 409. An unknown repository returns 404. The custom header `X-GitHub-Token` is ignored.
5. Insert a `WebhookEvent` with that user, the commit SHA, an empty diff, and `X-GitHub-Delivery`. The delivery id is unique. A second insert raises `IntegrityError`. If that delivery already has a review, the API returns 200. If a previous fetch failed and there is still no review, the same event is queued again.
6. After commit, Celery runs `fetch_webhook_diff`. That task loads the token from the linked user, calls the GitHub API with redirects disabled, saves the diff, creates a `Review` in language `diff`, and queues the existing `process_review` task.

`fetch_webhook_diff` retries timeouts, HTTP 429, rate-limit 403s, and 5xx responses. It records 401, other 403s, 404s, and redirects on `fetch_error` without an automatic retry.

## 8. Important folders and files

| Path | Role |
| --- | --- |
| `manage.py` | Django command entry point |
| `config/settings.py` | Database, JWT, Celery, LLM, and webhook settings |
| `config/urls.py` | Root routes |
| `config/celery.py` | Celery application |
| `apps/users/models.py` | User, daily limit, plaintext `github_token` |
| `apps/users/views.py` | Register, profile |
| `apps/users/serializers.py` | Registration and write-only GitHub token |
| `apps/reviews/models.py` | Review, Bug, SecurityIssue, QualitySuggestion, statuses |
| `apps/reviews/views.py` | Submit, detail, list, staff stats |
| `apps/reviews/tasks.py` | LLM task `process_review` |
| `apps/reviews/llm_service.py` | Provider HTTP calls and JSON parsing |
| `apps/webhooks/models.py` | `TrackedRepository`, `WebhookEvent` |
| `apps/webhooks/views.py` | Signature check and delivery claim |
| `apps/webhooks/tasks.py` | GitHub diff task `fetch_webhook_diff` |
| `apps/webhooks/serializers.py` | Unused serializer. No route imports it |
| `tests.py` | Auth, reviews, rate limit, staff stats |
| `tests_webhooks.py` | Webhook and diff-task tests |
| `conftest.py` | Forces pytest onto SQLite so local Postgres credentials are not required |

## 9. Why these technologies

- Django fits a CRUD API with users, admin, and migrations.
- DRF supplies authentication, serializers, and status codes without writing that plumbing by hand.
- PostgreSQL matches the relational shape: a review has many bugs.
- JWT fits mobile or frontend clients that are not using Django sessions.
- Celery is used because an LLM call is slower than a normal HTTP request. Redis is the broker Celery already supports.
- Groq is the default provider because the project already had a Groq chat-completions call. The provider can be switched with one setting.

## 10. Limitations and future improvements

Current limitations:

- A local review was completed with SQLite, Redis, Celery, and Groq. PostgreSQL was not verified.
- No live GitHub delivery was verified in this workspace.
- GitHub tokens are not encrypted at rest.
- There is no UI. Clients use HTTP or Django admin.
- Refresh tokens cannot be revoked, because the simplejwt blacklist app is not installed. Turning blacklist on without that app would crash refresh.
- A review stays `pending` until a worker is running.
- Very large GitHub comparisons can be partial. The event stores `diff_partial` when GitHub omits a patch or returns 300 files.
- The daily counter and the webhook path are correct for a demo. They are not a full multi-tenant GitHub App.

Possible later work, not part of this project:

- Encrypt `github_token` with a key that lives only in the environment, and migrate old rows on purpose.
- Add the simplejwt blacklist app if refresh tokens must be revoked.
- Add a small page that links a repository without using the admin.

## Request-to-response flow

Manual review:

```text
POST /api/review/  + Bearer token + {code, language}
  -> 401 if the token is missing or invalid
  -> 429 if the daily limit is reached
  -> 400 if code or language is invalid
  -> save Review(status=pending)
  -> queue process_review
  -> 202 {id, status, message}

Worker:
  pending -> processing -> completed
                       -> failed

GET /api/review/<id>/
  -> 200 with summary, score, bugs, security_issues, quality_suggestions
  -> 404 if the id belongs to someone else
```

Webhook:

```text
POST /webhooks/github/  + X-Hub-Signature-256 + X-GitHub-Delivery
  -> 403 if the signature or secret is wrong
  -> 200 if the event is not a useful push
  -> 404 if the repo is not linked
  -> 202 {webhook_event_id}

Worker fetch_webhook_diff:
  -> save diff
  -> create Review
  -> queue process_review
```

## Files to read first

1. `config/urls.py`
2. `config/settings.py`
3. `apps/users/models.py`
4. `apps/reviews/models.py`
5. `apps/reviews/views.py`
6. `apps/reviews/tasks.py`
7. `apps/reviews/llm_service.py`
8. `config/celery.py`
9. `apps/webhooks/views.py`
10. `apps/webhooks/tasks.py`
11. `apps/webhooks/models.py`
12. `tests.py` and `tests_webhooks.py`

## Interview questions

1. **What does this project do?**  
   It accepts code from an authenticated user, or a diff from a verified GitHub push, reviews it with an LLM in the background, and stores a score plus bugs, security notes, and quality suggestions.

2. **Why is the submit endpoint 202 instead of 200?**  
   202 means the job was accepted and is not finished. The LLM runs in Celery. The client polls the review detail endpoint.

3. **Why use Celery instead of calling the LLM in the view?**  
   Provider calls are slow and can fail. The HTTP request should stay short. The worker can retry a failed attempt without holding the user's connection open.

4. **What does Redis do?**  
   It is the Celery broker. `delay()` puts a message there. The worker takes the message and runs the Python function. Redis is not the database for reviews.

5. **Why `transaction.on_commit`?**  
   The task must not start before the review or webhook row is committed. Otherwise the worker can query an id that is not visible yet.

6. **How does login work?**  
   `POST /api/auth/login/` checks the email and password. SimpleJWT returns an access token and a refresh token. Later requests send `Authorization: Bearer <access>`.

7. **Where is the password stored?**  
   Django's user model hashes it in `create_user`. The API never returns the password hash. Registration marks the password write-only.

8. **Why can refresh fail in some JWT setups, and what did this project choose?**  
   `BLACKLIST_AFTER_ROTATION` needs `rest_framework_simplejwt.token_blacklist`. That app is not installed. The settings keep rotation and blacklist off, so refresh only issues a new access token and does not crash.

9. **How do you stop one user reading another user's review?**  
   Detail and list querysets filter `user=request.user`. A missing or foreign id is a 404 from the detail view.

10. **What are the review statuses?**  
    `pending` after save, `processing` when the worker starts, `completed` after findings are saved, `failed` when the worker is out of retries or the web process could not publish the Celery message.

11. **What does the LLM return?**  
    A JSON object. The task copies `summary` and `score` onto the review and creates `Bug`, `SecurityIssue`, and `QualitySuggestion` rows. The raw JSON is kept on `raw_llm_response`.

12. **What if the model returns Markdown or invalid JSON?**  
    The parser strips a fence. `json.loads` failure raises an error. The Celery task then retries once if it still can, otherwise it marks the review `failed`.

13. **How does the daily limit work?**  
    `User.check_and_increment_review_count` compares the counter with `settings.DAILY_REVIEW_LIMIT`. When `daily_review_reset` is in the past, the counter starts again at 1 and the reset time moves forward. The limit is not hardcoded to 10.

14. **Why is the webhook allowed without a JWT?**  
    GitHub cannot log in as an application user. `AllowAny` is paired with HMAC verification of the raw body using `GITHUB_WEBHOOK_SECRET`.

15. **Why is an empty webhook secret rejected?**  
    The old check treated a missing secret as success. That would accept forged payloads. The current check returns 403.

16. **How does the app know which user owns a GitHub repository?**  
    `TrackedRepository.full_name` points at one user. The webhook does not trust a token header from the caller. Staff create the link in the admin.

17. **How are duplicate GitHub deliveries handled?**  
    `WebhookEvent.delivery_id` is unique and comes from `X-GitHub-Delivery`. The second insert hits `IntegrityError`. If a review already exists, the response is 200 and no second review is created.

18. **Why is the GitHub diff downloaded in a task?**  
    GitHub waits only about 10 seconds for the webhook response. The compare API can be slower. The request only records the delivery and returns 202. `fetch_webhook_diff` does the HTTP call.

19. **Is the GitHub token encrypted?**  
    No. It is a `CharField` on the user. `PATCH /api/auth/profile/` can set it, and the serializer is `write_only`, so responses do not echo it. Logs in this code do not print it. The worker reads it from the database and sends it as a Bearer token to GitHub, with redirects disabled.

20. **What would you improve next, without pretending it is done?**  
    Encrypt existing tokens with an explicit migration, install the JWT blacklist app if revocation is required, and run one real webhook against a repository you control. None of those are implemented now.
