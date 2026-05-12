# AI Code Review API

Django backend service for automated code reviews using LLM (Groq).

**Live:** https://ai-code-review-api-7zpf.onrender.com
> First request may take 30-60 seconds (Render free tier)

## Features
- JWT authentication (access + refresh tokens)
- REST API for code submission and review retrieval
- GitHub webhook integration with HMAC validation
- Async code review processing via Celery + Redis
- Rate limiting (10 reviews/day, 429 with reset time)
- Structured LLM responses parsed into database fields (bugs, security, quality, score)

## Tech Stack
- Django + Django REST Framework
- PostgreSQL (Neon)
- Celery + Redis
- JWT Authentication (djangorestframework-simplejwt)
- Groq API (Llama)
- Deployed on Render

## Setup
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

## API Endpoints
- `POST /api/auth/register/` - Register
- `POST /api/auth/login/` - Login
- `POST /api/review/` - Submit code for review
- `GET /api/review/{id}/` - Get review result
- `GET /api/reviews/` - List reviews
- `POST /webhooks/github/` - GitHub webhook
- `GET /api/admin/stats/` - Stats (staff only)

## Rate Limits
- 10 reviews per day per user
- Resets at midnight UTC
- 429 response includes reset timestamp

## License
MIT
