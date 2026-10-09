"""Local web application; importing this package starts no server or jobs."""

from litsearch.web.api import create_app

__all__ = ["create_app"]
