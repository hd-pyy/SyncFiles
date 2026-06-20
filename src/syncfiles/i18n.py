from __future__ import annotations

from enum import StrEnum

from syncfiles.domain import ConflictAction


class Language(StrEnum):
    CHINESE = "zh"
    ENGLISH = "en"


DEFAULT_LANGUAGE = Language.CHINESE

LANGUAGE_LABELS: dict[Language, str] = {
    Language.CHINESE: "中文",
    Language.ENGLISH: "English",
}

LANGUAGE_BY_LABEL: dict[str, Language] = {label: language for language, label in LANGUAGE_LABELS.items()}

TRANSLATIONS: dict[str, dict[Language, str]] = {
    "app_title": {
        Language.CHINESE: "SyncFiles",
        Language.ENGLISH: "SyncFiles",
    },
    "device_status_unchecked": {
        Language.CHINESE: "设备状态：未检查",
        Language.ENGLISH: "Device status: unchecked",
    },
    "device_status_prefix": {
        Language.CHINESE: "设备状态：",
        Language.ENGLISH: "Device status: ",
    },
    "device_adb_missing": {
        Language.CHINESE: "未找到 ADB，请安装 Android Platform Tools 并加入 PATH。",
        Language.ENGLISH: "ADB is not installed or not on PATH.",
    },
    "device_no_device": {
        Language.CHINESE: "未连接 Android 设备。",
        Language.ENGLISH: "No Android device is connected.",
    },
    "device_unauthorized": {
        Language.CHINESE: "请在手机上允许 USB 调试授权。",
        Language.ENGLISH: "Authorize USB debugging on the phone.",
    },
    "device_multiple": {
        Language.CHINESE: "请只连接一台 Android 设备。",
        Language.ENGLISH: "Connect exactly one Android device.",
    },
    "device_ready": {
        Language.CHINESE: "已连接一台授权 Android 设备。",
        Language.ENGLISH: "One authorized Android device is ready.",
    },
    "label_language": {
        Language.CHINESE: "语言",
        Language.ENGLISH: "Language",
    },
    "button_check_device": {
        Language.CHINESE: "检查设备",
        Language.ENGLISH: "Check device",
    },
    "label_local_folder": {
        Language.CHINESE: "硬盘文件夹",
        Language.ENGLISH: "Hard drive folder",
    },
    "button_choose": {
        Language.CHINESE: "选择",
        Language.ENGLISH: "Choose",
    },
    "label_phone_folder": {
        Language.CHINESE: "手机文件夹",
        Language.ENGLISH: "Phone folder",
    },
    "button_browse_phone": {
        Language.CHINESE: "浏览手机",
        Language.ENGLISH: "Browse phone",
    },
    "button_scan": {
        Language.CHINESE: "扫描差异",
        Language.ENGLISH: "Scan differences",
    },
    "button_start_sync": {
        Language.CHINESE: "开始同步",
        Language.ENGLISH: "Start sync",
    },
    "tab_phone_to_local": {
        Language.CHINESE: "手机 -> 硬盘",
        Language.ENGLISH: "Phone -> hard drive",
    },
    "tab_local_to_phone": {
        Language.CHINESE: "硬盘 -> 手机",
        Language.ENGLISH: "Hard drive -> phone",
    },
    "tab_conflicts": {
        Language.CHINESE: "冲突",
        Language.ENGLISH: "Conflicts",
    },
    "label_log": {
        Language.CHINESE: "日志",
        Language.ENGLISH: "Log",
    },
    "dialog_choose_local": {
        Language.CHINESE: "选择硬盘文件夹",
        Language.ENGLISH: "Choose hard drive folder",
    },
    "dialog_choose_phone": {
        Language.CHINESE: "选择手机文件夹",
        Language.ENGLISH: "Choose phone folder",
    },
    "dialog_adb_error": {
        Language.CHINESE: "ADB 错误",
        Language.ENGLISH: "ADB error",
    },
    "button_open": {
        Language.CHINESE: "打开",
        Language.ENGLISH: "Open",
    },
    "button_choose_this_folder": {
        Language.CHINESE: "选择此文件夹",
        Language.ENGLISH: "Choose this folder",
    },
    "dialog_missing_folders_title": {
        Language.CHINESE: "缺少文件夹",
        Language.ENGLISH: "Missing folders",
    },
    "dialog_missing_folders_message": {
        Language.CHINESE: "请先选择两个文件夹再扫描。",
        Language.ENGLISH: "Choose both folders before scanning.",
    },
    "dialog_busy_title": {
        Language.CHINESE: "正在处理",
        Language.ENGLISH: "Busy",
    },
    "dialog_busy_message": {
        Language.CHINESE: "当前正在扫描或同步，请等待完成后再操作。",
        Language.ENGLISH: "A scan or sync is already running. Wait for it to finish.",
    },
    "dialog_no_phone_selection_title": {
        Language.CHINESE: "未选择文件夹",
        Language.ENGLISH: "No folder selected",
    },
    "dialog_no_phone_selection_message": {
        Language.CHINESE: "请先在列表中选择一个手机文件夹。",
        Language.ENGLISH: "Select a phone folder from the list first.",
    },
    "log_scanning_local": {
        Language.CHINESE: "正在扫描硬盘文件夹...",
        Language.ENGLISH: "Scanning hard drive folder...",
    },
    "log_scanning_phone": {
        Language.CHINESE: "正在扫描手机文件夹...",
        Language.ENGLISH: "Scanning phone folder...",
    },
    "log_scan_complete": {
        Language.CHINESE: "扫描完成：{phone_to_local} 个手机到硬盘，{local_to_phone} 个硬盘到手机，{conflicts} 个冲突。",
        Language.ENGLISH: "Scan complete: {phone_to_local} phone-to-hard-drive, {local_to_phone} hard-drive-to-phone, {conflicts} conflicts.",
    },
    "dialog_conflict_action": {
        Language.CHINESE: "冲突处理",
        Language.ENGLISH: "Conflict action",
    },
    "dialog_no_scan_title": {
        Language.CHINESE: "尚未扫描",
        Language.ENGLISH: "No scan",
    },
    "dialog_no_scan_message": {
        Language.CHINESE: "请先扫描差异再同步。",
        Language.ENGLISH: "Scan differences before syncing.",
    },
    "dialog_confirm_sync_title": {
        Language.CHINESE: "确认同步",
        Language.ENGLISH: "Confirm sync",
    },
    "dialog_confirm_sync_message": {
        Language.CHINESE: "现在执行列表中的复制操作吗？",
        Language.ENGLISH: "Run the listed copy operations now?",
    },
    "log_pushed": {
        Language.CHINESE: "已推送 {path}",
        Language.ENGLISH: "Pushed {path}",
    },
    "log_pulled": {
        Language.CHINESE: "已拉取 {path}",
        Language.ENGLISH: "Pulled {path}",
    },
    "log_sync_complete": {
        Language.CHINESE: "同步完成：已尝试 {count} 个操作。",
        Language.ENGLISH: "Sync complete: {count} operations attempted.",
    },
    "dialog_error_title": {
        Language.CHINESE: "SyncFiles 错误",
        Language.ENGLISH: "SyncFiles error",
    },
    "log_error": {
        Language.CHINESE: "错误：{message}",
        Language.ENGLISH: "Error: {message}",
    },
}

CONFLICT_ACTION_LABELS: dict[ConflictAction, dict[Language, str]] = {
    ConflictAction.USE_PHONE: {
        Language.CHINESE: "使用手机版本",
        Language.ENGLISH: "Use phone version",
    },
    ConflictAction.USE_LOCAL: {
        Language.CHINESE: "使用硬盘版本",
        Language.ENGLISH: "Use hard drive version",
    },
    ConflictAction.KEEP_BOTH: {
        Language.CHINESE: "保留双方",
        Language.ENGLISH: "Keep both",
    },
    ConflictAction.SKIP: {
        Language.CHINESE: "跳过",
        Language.ENGLISH: "Skip",
    },
}


def text(key: str, language: Language = DEFAULT_LANGUAGE, **values: object) -> str:
    template = TRANSLATIONS[key][language]
    if values:
        return template.format(**values)
    return template


def conflict_action_label(action: ConflictAction, language: Language = DEFAULT_LANGUAGE) -> str:
    return CONFLICT_ACTION_LABELS[action][language]
