from app.utils import guess_content_type, is_allowed_file


def test_assessment_image_types_are_allowed() -> None:
    for filename in ("page.png", "page.jpg", "page.jpeg"):
        assert is_allowed_file(filename)

    assert guess_content_type("page.png") == "image/png"
    assert guess_content_type("page.jpg") == "image/jpeg"


def test_unsupported_file_type_remains_rejected() -> None:
    assert not is_allowed_file("payload.exe")