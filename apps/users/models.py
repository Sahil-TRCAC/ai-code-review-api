from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    email = models.EmailField(unique=True)
    daily_review_count = models.IntegerField(default=0)
    daily_review_reset = models.DateTimeField(default=timezone.now)
    github_token = models.CharField(max_length=500, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    class Meta:
        db_table = 'users'

    def check_and_increment_review_count(self):
        now = timezone.now()
        if now >= self.daily_review_reset:
            self.daily_review_count = 1
            self.daily_review_reset = now.replace(hour=0, minute=0, second=0, microsecond=0) + timezone.timedelta(days=1)
            self.save()
            return True
        if self.daily_review_count >= 10:
            return False
        self.daily_review_count += 1
        self.save()
        return True

    def get_remaining_reviews(self):
        from django.conf import settings
        now = timezone.now()
        if now >= self.daily_review_reset:
            return settings.DAILY_REVIEW_LIMIT
        return max(0, settings.DAILY_REVIEW_LIMIT - self.daily_review_count)
