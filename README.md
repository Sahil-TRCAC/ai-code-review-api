# AI Code Review API

A production-grade Django REST API that automatically reviews code using LLM (Groq). Submit code via API or GitHub webhook and receive structured feedback covering bugs, security issues, and code quality — with async processing, JWT auth, and per-user rate limiting.

**Live:** https://ai-code-review-api-7zpf.onrender.com
> Hosted on Render free tier. First request may take 30-60 seconds to wake up.

**GitHub:** https://github.com/Sahil-TRCAC/ai-code-review-api

---

## Features

- JWT authentication (register, login, access + refresh tokens)
- REST API for code submission and structured review retrieval
- GitHub webhook integration with HMAC signature validation
- Async code review processing via Celery + Redis
- Per-user rate limiting (10 reviews/day, 429 with reset time)
- Structured LLM responses parsed into relational DB fields (bugs, security issues, quality suggestions, score)
- Staff-only admin stats endpoint

## Tech Stack

- **Backend:** Django + Django REST Framework
- **Database:** PostgreSQL (Neon)
- **Queue:** Celery + Redis
- **Auth:** JWT (djangorestframework-simplejwt)
- **LLM:** Groq API (Llama)
- **Deployment:** Render

---

## Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Environment variables
cp .env.example .env
# Edit .env with your API keys and settings

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Start development server
python manage.py runserver

# Start Celery worker (separate terminal)
celery -A config worker --loglevel=info --pool=solo
```

---

## API Endpoints

### Authentication
- `POST /api/auth/register/` — User registration
- `POST /api/auth/login/` — Get JWT tokens
- `POST /api/auth/refresh/` — Refresh access token

### Code Review
- `POST /api/review/` — Submit code for review
- `GET /api/review/{id}/` — Get review result
- `GET /api/reviews/` — List user's reviews (paginated, filterable)

### Webhooks
- `POST /webhooks/github/` — GitHub webhook endpoint

### Admin (Staff only)
- `GET /api/admin/stats/` — Review statistics

---

## Example Requests

### Register
```bash
curl -X POST https://ai-code-review-api-7zpf.onrender.com/api/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{"email": "you@email.com", "username": "you", "password": "Pass1234!", "password_confirm": "Pass1234!"}'
```

### Submit Code for Review
```bash
curl -X POST https://ai-code-review-api-7zpf.onrender.com/api/review/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "def divide(a, b):\n    return a / b",
    "language": "python"
  }'
```

### Get Review Result
```bash
curl https://ai-code-review-api-7zpf.onrender.com/api/review/1/ \
  -H "Authorization: Bearer <token>"
```

### GitHub Webhook
Configure in GitHub repo settings:
- Payload URL: `https://ai-code-review-api-7zpf.onrender.com/webhooks/github/`
- Content type: `application/json`
- Secret: Your webhook secret

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key |
| `DEBUG` | Enable debug mode |
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `GROQ_API_KEY` | Groq API key |
| `LLM_PROVIDER` | `groq`, `anthropic`, or `openai` |
| `JWT_ACCESS_TOKEN_LIFETIME` | Access token lifetime in minutes |
| `JWT_REFRESH_TOKEN_LIFETIME` | Refresh token lifetime in minutes |
| `DAILY_REVIEW_LIMIT` | Reviews per day per user (default: 10) |
| `GITHUB_WEBHOOK_SECRET` | Secret for GitHub webhook HMAC validation |

---

## Rate Limiting

- Free tier: 10 reviews per day per user
- Counter resets at midnight UTC
- `429 Too Many Requests` response includes reset timestamp

---

## Testing

The project includes automated pytest test suites covering:

* User registration and JWT authentication
* Profile access and authorization
* Code review submission workflows
* Review retrieval and pagination
* Daily rate-limiting enforcement (429 responses)
* Staff-only admin endpoint access control

Run tests:

```bash
pytest
```


## License

MIT
