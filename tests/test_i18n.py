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


def test_sync_mode_and_left_right_labels_are_translated() -> None:
    assert text("sync_mode_hard_drive", Language.ENGLISH) == "Hard drive <-> hard drive"
    assert text("sync_mode_phone", Language.ENGLISH) == "Hard drive <-> phone"
    assert text("label_left_folder", Language.ENGLISH) == "Left hard drive folder"
    assert text("label_right_folder", Language.ENGLISH) == "Right hard drive folder"
    assert text("tab_left_to_right", Language.ENGLISH) == "Left -> right"
    assert text("tab_right_to_left", Language.ENGLISH) == "Right -> left"
