"""Test helpers for dashboard frontend routes."""

from contextlib import contextmanager
import os
import tempfile
from unittest import mock

from pi.apps.van_dashboard.routes import common as dashboard_common_routes


@contextmanager
def react_index_page(client):
    """Serve a temporary React index page for one test-client request."""
    with tempfile.TemporaryDirectory() as directory:
        with open(os.path.join(directory, "index.html"), "wb") as handle:
            handle.write(b"<!doctype html><main>React dashboard</main>")
        with mock.patch.object(
            dashboard_common_routes, "REACT_FRONTEND_ROOT", directory
        ):
            response = client.get("/")
            try:
                yield response
            finally:
                response.close()
