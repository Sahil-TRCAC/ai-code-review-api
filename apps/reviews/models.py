from django.db import models
from django.conf import settings


class ReviewStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    PROCESSING = 'processing', 'Processing'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'


class Severity(models.TextChoices):
    HIGH = 'high', 'High'
    MEDIUM = 'medium', 'Medium'
    LOW = 'low', 'Low'


class Review(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews')
    code = models.TextField()
    language = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    summary = models.TextField(blank=True, null=True)
    score = models.IntegerField(blank=True, null=True)
    raw_llm_response = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    celery_task_id = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = 'reviews'
        ordering = ['-created_at']

    def __str__(self):
        return f"Review {self.id} - {self.language}"


class Bug(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='bugs')
    line = models.IntegerField(blank=True, null=True)
    issue = models.TextField()
    severity = models.CharField(max_length=10, choices=Severity.choices)

    class Meta:
        db_table = 'bugs'


class SecurityIssue(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='security_issues')
    line = models.IntegerField(blank=True, null=True)
    issue = models.TextField()
    severity = models.CharField(max_length=10, choices=Severity.choices)

    class Meta:
        db_table = 'security_issues'


class QualitySuggestion(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='quality_suggestions')
    suggestion = models.TextField()
    severity = models.CharField(max_length=10, choices=Severity.choices)

    class Meta:
        db_table = 'quality_suggestions'
