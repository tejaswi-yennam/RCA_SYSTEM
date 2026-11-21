from src.processor import double_value


def test_double_value():
    assert double_value({'value': '5'}) == 10
