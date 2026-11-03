"""A platform behind a gateway: answers that aren't the platform's own (a 502 page) and ones that are."""
import requests

from fabric_desktop.central.client import FabricPlatform

PAGE = b"<html>\n<head><title>502 Bad Gateway</title></head>\n<body>Bad Gateway</body>\n</html>"


def gateway(svc, status, content, reason=""):
    resp = requests.Response()
    resp.status_code, resp._content, resp.reason = status, content, reason
    resp._content_consumed = True  # read in full, as requests leaves a response it has read
    session = type("Session", (), {"post": lambda self, url, **kw: resp})()
    svc._catalog = lambda: FabricPlatform(lambda: "tok", "ws", "fn", session=session, sleep=lambda _s: None)
