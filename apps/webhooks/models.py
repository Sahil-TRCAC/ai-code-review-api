from django.db import models
from django.db.models.functions import Lower

from apps.reviews.models import Review


class RepositoryLinkError(Exception):
    """More than one tracked repository matches the payload name."""


class TrackedRepository(models.Model):
    """Links a GitHub repository to the application user who should own its reviews."""

    user = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='tracked_repositories')
    full_name = models.CharField(max_length=255, unique=True)

    class Meta:
        db_table = 'tracked_repositories'
        constraints = [
            models.UniqueConstraint(
                Lower('full_name'),
                name='tracked_repository_full_name_ci_uniq',
            ),
        ]

    def __str__(self):
        return self.full_name

    @classmethod
    def resolve(cls, full_name):
        matches = list(cls.objects.select_related('user').filter(full_name__iexact=full_name)[:2])
        if not matches:
            return None
        if len(matches) > 1:
            raise RepositoryLinkError(full_name)
        return matches[0]


class WebhookEvent(models.Model):
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='webhook_events')
    repo_name = models.CharField(max_length=255)
    commit_hash = models.CharField(max_length=40)
    before_sha = models.CharField(max_length=40, blank=True, default='')
    default_branch = models.CharField(max_length=255, blank=True, default='')
    diff = models.TextField(blank=True, default='')
    diff_partial = models.BooleanField(
        default=False,
        help_text='GitHub truncated this comparison. The stored diff is only the returned portion.',
    )
    fetch_error = models.CharField(max_length=255, blank=True, default='')
    review = models.ForeignKey(Review, on_delete=models.SET_NULL, null=True, blank=True, related_name='webhook_event')
    delivery_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)

    class Meta:
        db_table = 'webhook_events'

    def __str__(self):
        return f"{self.repo_name} - {self.commit_hash[:7]}"
