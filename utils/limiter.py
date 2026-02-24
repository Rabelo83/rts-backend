"""
Shared Flask-Limiter instance.
Call limiter.init_app(app) in the app factory, then import and decorate routes.
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
