import os
from pathlib import Path
import sys

import django
from django.conf import settings

ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import pytest

pytest_plugins = ['pytest_django']


def pytest_configure():
    settings.DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    }
    django.setup()
