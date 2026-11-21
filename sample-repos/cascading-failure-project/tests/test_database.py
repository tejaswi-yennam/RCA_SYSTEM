from src.database import connect


def test_database_connect():
    assert connect()
