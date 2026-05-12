import os
from celery import Celery
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')
app.conf.task_always_eager = True
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()