from src.divider import divide


def test_divide_by_zero():
    assert divide(1, 0) == 0
