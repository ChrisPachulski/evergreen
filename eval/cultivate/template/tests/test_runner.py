from toolbox.runner import normalize_title


def test_normalize_title_smoke() -> None:
    normalize_title("a small tool")
