from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix
import web_app

dash = web_app.app
dash.config["APPLICATION_ROOT"] = "/nodes"
dash.config["SESSION_COOKIE_PATH"] = "/nodes/"
dash.config["SESSION_COOKIE_SECURE"] = True
dash.config["SESSION_COOKIE_SAMESITE"] = "Strict"
dash.wsgi_app = ProxyFix(dash.wsgi_app, x_for=1, x_proto=1)


def _not_found(environ, start_response):
    start_response("404 NOT FOUND", [("Content-Type", "text/plain; charset=utf-8")])
    return [b"Not Found"]


application = DispatcherMiddleware(_not_found, {"/nodes": dash})
