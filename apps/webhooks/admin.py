from django.contrib import admin

from .models import TrackedRepository, WebhookEvent


@admin.register(TrackedRepository)
class TrackedRepositoryAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'user')
    search_fields = ('full_name',)


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ('repo_name', 'commit_hash', 'delivery_id', 'processed', 'diff_partial', 'received_at')
    search_fields = ('repo_name', 'delivery_id', 'commit_hash')
    readonly_fields = ('delivery_id', 'fetch_error', 'diff_partial')
