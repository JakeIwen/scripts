"""Van dashboard application."""


def create_app():
    from .van_dashboard import create_app as factory

    return factory()
