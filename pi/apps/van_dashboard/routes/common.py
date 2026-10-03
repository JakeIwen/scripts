"""Common dashboard routes."""

import os

from flask import Blueprint, current_app as app, jsonify, send_from_directory

from ..van_dashboard_common import REACT_FRONTEND_ROOT


bp = Blueprint("common", __name__)



APP_ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" rx="112" fill="#19232d"/>
<path d="M106 178h300l35 74v142c0 17-13 30-30 30H101c-17 0-30-13-30-30V252l35-74Z" fill="#51b7c6"/>
<path d="M136 116h240l30 136H106l30-136Z" fill="#dbe9ee"/>
<path d="M165 141h182l17 86H148l17-86Z" fill="#22313d"/>
<circle cx="145" cy="385" r="42" fill="#111820"/><circle cx="367" cy="385" r="42" fill="#111820"/>
<path d="M216 303h80" stroke="#ef503f" stroke-width="30" stroke-linecap="round"/>
</svg>"""



@bp.route("/manifest.webmanifest")
def manifest():
    response = jsonify(
        {
            "name": "Van Dashboard",
            "short_name": "Van",
            "id": "/",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#111820",
            "theme_color": "#111820",
            "icons": [{"src": "/app-icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        }
    )
    response.mimetype = "application/manifest+json"
    return response



@bp.route("/app-icon.svg")
def app_icon():
    response = app.response_class(APP_ICON, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response



@bp.route("/assets/<path:filename>")
def react_asset(filename):
    response = send_from_directory(os.path.join(REACT_FRONTEND_ROOT, "assets"), filename)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response



@bp.route("/")
def index():
    if os.path.isfile(os.path.join(REACT_FRONTEND_ROOT, "index.html")):
        response = send_from_directory(REACT_FRONTEND_ROOT, "index.html")
        response.headers["Cache-Control"] = "no-store"
        return response
    return app.response_class(
        "React dashboard build is unavailable\n",
        status=503,
        mimetype="text/plain",
    )
