from syncfiles.domain import ConflictAction
from syncfiles.i18n import DEFAULT_LANGUAGE, LANGUAGE_LABELS, Language, conflict_action_label, text


def test_default_language_is_chinese() -> None:
    assert DEFAULT_LANGUAGE is Language.CHINESE
    assert LANGUAGE_LABELS[Language.CHINESE] == "中文"
    assert LANGUAGE_LABELS[Language.ENGLISH] == "English"


def test_translates_known_ui_text_in_chinese_and_english() -> None:
    assert text("button_scan", Language.CHINESE) == "扫描差异"
    assert text("button_scan", Language.ENGLISH) == "Scan differences"


def test_translates_conflict_action_labels() -> None:
    assert conflict_action_label(ConflictAction.SKIP, Language.CHINESE) == "跳过"
    assert conflict_action_label(ConflictAction.USE_LOCAL, Language.ENGLISH) == "Use hard drive version"
