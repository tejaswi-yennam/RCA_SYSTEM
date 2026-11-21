from src.service import initialise_service


def test_api_startup():
    assert initialise_service()['connection']
