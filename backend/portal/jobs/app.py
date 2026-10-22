"""The procrastinate app, shared by every job module.

Procrastinate's Django integration builds the app from the database settings,
records jobs in `core`, and autodiscovers `jobs.py` in each installed app
(PROCRASTINATE_AUTODISCOVER_MODULE_NAME in settings). Import `app` from here
rather than from procrastinate directly so the wiring lives in one place.
"""

from procrastinate.contrib.django import app

__all__ = ["app"]
