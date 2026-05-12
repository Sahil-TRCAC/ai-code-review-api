from django.db import models
from apps.reviews.models import Review


class WebhookEvent(models.Model):
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='webhook_events')
    repo_name = models.CharField(max_length=255)
    commit_hash = models.CharField(max_length=40)
    diff = models.TextField()
    review = models.ForeignKey(Review, on_delete=models.SET_NULL, null=True, blank=True, related_name='webhook_event')
    received_at = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)

    class Meta:
        db_table = 'webhook_events'

    def __str__(self):
        return f"{self.repo_name} - {self.commit_hash[:7]}"
