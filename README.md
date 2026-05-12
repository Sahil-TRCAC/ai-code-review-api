# AI Code Review API

Django backend service for automated code reviews using LLM (Claude/GPT).

## Features

- JWT authentication (access + refresh tokens)
- REST API for code submission and review retrieval
- GitHub webhook integration
- Async code review processing via Celery + Redis
- Rate limiting (10 reviews/day for free tier)
- Structured LLM responses parsed into database fields

## Tech Stack

- Django + Django REST Framework
- PostgreSQL
- Celery + Redis
- JWT Authentication (djangorestframework-simplejwt)
- Anthropic Claude API / OpenAI API

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
celery -A config worker -l info
```

## API Endpoints

### Authentication
- `POST /api/auth/register/` - User registration
- `POST /api/auth/login/` - Get JWT tokens
- `POST /api/auth/refresh/` - Refresh access token

### Code Review
- `POST /api/review/` - Submit code for review
- `GET /api/review/{id}/` - Get review result
- `GET /api/reviews/` - List user's reviews (paginated)

### Webhooks
- `POST /webhooks/github/` - GitHub webhook endpoint

### Admin (Staff only)
- `GET /api/admin/stats/` - Review statistics

## Example Requests

### Submit Code for Review
```bash
curl -X POST http://localhost:8000/api/review/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "def hello(): print(\"Hello, World!\")",
    "language": "python"
  }'
```

### Get Review Result
```bash
curl http://localhost:8000/api/review/{review_id}/ \
  -H "Authorization: Bearer <token>"
```

### GitHub Webhook
Configure in GitHub repo settings:
- Payload URL: `https://your-domain.com/webhooks/github/`
- Content type: `application/json`
- Secret: Your webhook secret

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key |
| `DEBUG` | Enable debug mode |
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `LLM_PROVIDER` | `anthropic` or `openai` |
| `JWT_ACCESS_TOKEN_LIFETIME` | Access token lifetime |
| `JWT_REFRESH_TOKEN_LIFETIME` | Refresh token lifetime |

## Rate Limits

- Free tier: 10 reviews per day per user
- Counter resets at midnight UTC
- 429 response includes `X-RateLimit-Reset` header

## License

MIT
