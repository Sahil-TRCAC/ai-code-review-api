from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests
from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.reviews.models import Review
from apps.reviews.tasks import process_review

from .models import RepositoryLinkError, TrackedRepository, WebhookEvent

GITHUB_API = 'https://api.github.com'
ZERO_SHA = '0' * 40
PARTIAL_NOTE = (
    'GitHub truncated this comparison. The stored diff includes only the portion GitHub returned.\n'
)
MAX_RETRY_WAIT = 300


class RetryableGitHubError(Exception):
    def __init__(self, message, countdown=None):
        super().__init__(message)
        self.countdown = countdown


class PermanentGitHubError(Exception):
    pass


def _worker_will_retry(task):
    request = task.request
    if getattr(request, 'called_directly', True):
        return False
    if task.max_retries is None:
        return True
    return request.retries < task.max_retries


def _retry_after_seconds(response):
    value = (response.headers.get('Retry-After') or '').strip()
    if not value:
        return None
    if value.isdigit():
        return min(int(value), MAX_RETRY_WAIT)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if timezone.is_naive(when):
        when = timezone.make_aware(when, timezone.utc)
    delay = int((when - timezone.now()).total_seconds())
    return max(0, min(delay, MAX_RETRY_WAIT))


def _repo_url(full_name, suffix):
    owner, _, name = full_name.partition('/')
    return f"{GITHUB_API}/repos/{quote(owner, safe='')}/{quote(name, safe='')}{suffix}"


def _github_get(url, token):
    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'ai-code-review-api',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=False)
    except requests.Timeout as exc:
        raise RetryableGitHubError('GitHub request timed out') from exc
    except requests.RequestException as exc:
        raise RetryableGitHubError('GitHub request failed') from exc

    if 300 <= response.status_code < 400:
        raise PermanentGitHubError(f'GitHub redirect blocked ({response.status_code})')
    remaining = response.headers.get('X-RateLimit-Remaining')
    if response.status_code == 429 or (response.status_code == 403 and remaining == '0'):
        raise RetryableGitHubError(
            f'GitHub rate limit reached ({response.status_code})',
            countdown=_retry_after_seconds(response),
        )
    if 500 <= response.status_code <= 599:
        raise RetryableGitHubError(
            f'GitHub temporary failure ({response.status_code})',
            countdown=_retry_after_seconds(response),
        )
    if response.status_code in (401, 403, 404):
        raise PermanentGitHubError(f'GitHub request rejected ({response.status_code})')
    if response.status_code != 200:
        raise PermanentGitHubError(f'GitHub request failed ({response.status_code})')
    try:
        payload = response.json()
    except ValueError as exc:
        raise PermanentGitHubError('GitHub returned a non-JSON response') from exc
    if not isinstance(payload, dict):
        raise PermanentGitHubError('GitHub returned an unexpected response')
    return payload


def _render_files(files):
    if not isinstance(files, list):
        raise PermanentGitHubError('GitHub file list was malformed')
    chunks = []
    omitted = 0
    for entry in files:
        if not isinstance(entry, dict):
            raise PermanentGitHubError('GitHub file list was malformed')
        name = entry.get('filename') or 'unknown'
        patch = entry.get('patch')
        if isinstance(patch, str) and patch.strip():
            chunks.append(f'diff --git a/{name} b/{name}\n{patch}')
        else:
            omitted += 1
    partial = len(files) >= 300 or omitted > 0
    body = '\n'.join(chunks)
    if partial and body:
        body = PARTIAL_NOTE + body
    return body, partial and bool(files)


def _mark_terminal(event, message):
    event.processed = True
    event.fetch_error = str(message)[:255]
    event.save(update_fields=['processed', 'fetch_error'])


def _default_branch(repo_name, token):
    payload = _github_get(_repo_url(repo_name, ''), token)
    branch = payload.get('default_branch')
    if not isinstance(branch, str) or not branch.strip():
        raise PermanentGitHubError('GitHub did not return a default branch')
    return branch.strip()


def retrieve_push_diff(event, token):
    """Return (diff, partial). An empty diff means GitHub reported no file changes."""
    before = event.before_sha or ''
    after = event.commit_hash
    if before and before != ZERO_SHA and after and after != ZERO_SHA:
        payload = _github_get(
            _repo_url(event.repo_name, f"/compare/{quote(before, safe='')}...{quote(after, safe='')}"),
            token,
        )
        return _render_files(payload.get('files') or [])

    if not after or after == ZERO_SHA:
        return '', False

    base = (event.default_branch or '').strip()
    if not base:
        base = _default_branch(event.repo_name, token)
    try:
        payload = _github_get(
            _repo_url(event.repo_name, f"/compare/{quote(base, safe='')}...{quote(after, safe='')}"),
            token,
        )
    except PermanentGitHubError as exc:
        if '(404)' not in str(exc):
            raise
        payload = None
    if payload is not None:
        diff, partial = _render_files(payload.get('files') or [])
        if diff or partial:
            return diff, partial

    payload = _github_get(
        _repo_url(event.repo_name, f"/commits/{quote(after, safe='')}"),
        token,
    )
    return _render_files(payload.get('files') or [])


def _save_review(event_id, diff, partial):
    with transaction.atomic():
        event = WebhookEvent.objects.select_related('user').get(pk=event_id)
        if event.review_id:
            return None
        review = Review.objects.create(user=event.user, code=diff, language='diff')
        linked = WebhookEvent.objects.filter(pk=event_id, review__isnull=True).update(
            review=review,
            diff=diff,
            diff_partial=partial,
            processed=True,
            fetch_error='',
        )
        if linked != 1:
            review.delete()
            return None
        review_id = review.id
        transaction.on_commit(lambda: process_review.delay(review_id))
        return review


@shared_task(bind=True, max_retries=3)
def fetch_webhook_diff(self, event_id: int):
    try:
        event = WebhookEvent.objects.select_related('user').get(pk=event_id)
    except WebhookEvent.DoesNotExist:
        return {'event_id': event_id, 'status': 'missing'}

    if event.review_id or (event.processed and not event.fetch_error):
        return {'event_id': event_id, 'status': 'already_processed'}

    try:
        tracked = TrackedRepository.resolve(event.repo_name)
    except RepositoryLinkError:
        _mark_terminal(event, 'Repository link is ambiguous')
        return {'event_id': event_id, 'status': 'ambiguous'}
    if tracked is None or tracked.user_id != event.user_id:
        _mark_terminal(event, 'Repository is not linked to this user')
        return {'event_id': event_id, 'status': 'unlinked'}

    try:
        diff, partial = retrieve_push_diff(event, tracked.user.github_token)
    except RetryableGitHubError as exc:
        if _worker_will_retry(self):
            raise self.retry(exc=exc, countdown=exc.countdown)
        _mark_terminal(event, str(exc))
        return {'event_id': event_id, 'status': 'retry_exhausted'}
    except PermanentGitHubError as exc:
        _mark_terminal(event, str(exc))
        return {'event_id': event_id, 'status': 'permanent_failure'}

    if not diff:
        if partial:
            _mark_terminal(event, PARTIAL_NOTE.strip())
            return {'event_id': event_id, 'status': 'truncated_empty'}
        event.processed = True
        event.fetch_error = ''
        event.diff_partial = False
        event.save(update_fields=['processed', 'fetch_error', 'diff_partial'])
        return {'event_id': event_id, 'status': 'no_changes'}

    review = _save_review(event.id, diff, partial)
    if review is None:
        return {'event_id': event_id, 'status': 'already_processed'}
    return {'event_id': event_id, 'status': 'queued', 'review_id': review.id}
