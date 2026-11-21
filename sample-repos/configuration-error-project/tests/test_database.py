from src.database import connect


def test_connect_returns_url():
    assert connect().startswith('postgres')
