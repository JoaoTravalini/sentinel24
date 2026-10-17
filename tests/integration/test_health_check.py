import os
from urllib.request import urlopen


def test_health_check_returns_no_content():
    host = os.environ["SERVER_LOCALHOST"]
    port = os.environ.get("API_PORT", "3000")
    url = f"http://{host}:{port}/health_check"

    with urlopen(url, timeout=5) as response:
        assert response.status == 204
        assert response.read() == b""