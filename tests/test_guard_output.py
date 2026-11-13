from image_gateway.policy import parse_guard_output


def test_unsafe_with_category():
    result = parse_guard_output("Safety: Unsafe\nCategories: Sexual Content or Sexual Acts")
    assert result.label == "Unsafe" and result.categories == ["Sexual Content or Sexual Acts"]


def test_safe_with_none():
    result = parse_guard_output("Safety: Safe\nCategories: None")
    assert result.label == "Safe" and result.categories == ["None"]


def test_several_categories():
    result = parse_guard_output("Safety: Controversial\nCategories: Violent, Unethical Acts")
    assert result.categories == ["Violent", "Unethical Acts"]


def test_garbage_has_no_label():
    assert parse_guard_output("I cannot help with that").label is None
