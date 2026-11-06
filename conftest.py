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
    # pytest-django reads DATABASES before this hook. Replace the default
    # entry in place so an already cached connection configuration sees sqlite.
    settings.DATABASES.clear()
    settings.DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
    from django.db import connections
    connections.__dict__.pop('settings', None)
    connections.close_all()
    storage = getattr(connections._connections, '_storage', None)
    if storage is not None:
        for alias in list(vars(storage)):
            if not alias.startswith('_'):
                delattr(storage, alias)
    django.setup()
