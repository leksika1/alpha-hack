import httpx
import pytest

from tests.e2e.support import BASE_URL, login


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    if not BASE_URL:
        pytest.fail("E2E_BASE_URL is not set: run e2e via `make e2e`")
    return httpx.Client(base_url=BASE_URL, timeout=60)


@pytest.fixture(scope="session")
def user(client: httpx.Client) -> dict[str, str]:
    return login(client, "demo", "demo12345")


@pytest.fixture(scope="session")
def admin(client: httpx.Client) -> dict[str, str]:
    return login(client, "admin", "admin12345")
