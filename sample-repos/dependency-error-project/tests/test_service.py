from src.service import build_message


def test_build_message():
    assert build_message('Ada') == 'Hello Ada'
