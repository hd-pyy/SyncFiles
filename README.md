# SyncFiles

SyncFiles 是一个 Windows 优先的桌面同步工具,用于在两个文件夹之间同步文件。支持:

- 硬盘 ↔ 硬盘
- 硬盘 ↔ Android 手机(通过 ADB)

应用默认以中文启动,可在主窗口通过 `中文` / `English` 切换语言。

它执行**双向补全式同步**:

- 一边缺失的文件会从另一边复制过来
- 同名文件大小或修改时间不一致时,标记为冲突
- 不会传播删除操作

## 运行环境

- Python 3.11 或更高版本
- Android Platform Tools,`adb` 在 `PATH` 中(仅手机同步模式需要)
- 一台开启了 USB 调试并已授权的 Android 手机(仅手机同步模式需要)

> **最终用户无需满足以上条件。** 如果你只是想运行打包好的可执行文件,直接看 [打包好的 Windows 可执行文件](#打包好的-windows-可执行文件) 一节。

## 开发

```powershell
python -m pip install -e .[dev]
python -m pytest
python -m syncfiles
```

## 打包好的 Windows 可执行文件

本项目自带 PyInstaller 打包配置,产物是**单一 `.exe` 文件**,内含:

- Python 3.13 运行时
- Tcl/Tk
- syncfiles 包本体
- `adb.exe` 及 6 个运行所需 DLL(AdbWinApi、AdbWinUsbApi、libwinpthread-1、vcruntime140、msvcp140、adb 主程序)

因此**最终用户无需安装 Python,无需安装 platform-tools**,双击 exe 即可在手机模式下使用。

### 构建步骤

1. **准备 adb fallback 文件**(一次性操作,这些二进制不进版本库)

   ```powershell
   mkdir src\syncfiles\adb_fallback
   copy "<你的-platform-tools>\adb.exe"              src\syncfiles\adb_fallback\
   copy "<你的-platform-tools>\AdbWinApi.dll"        src\syncfiles\adb_fallback\
   copy "<你的-platform-tools>\AdbWinUsbApi.dll"     src\syncfiles\adb_fallback\
   copy "<你的-platform-tools>\libwinpthread-1.dll"  src\syncfiles\adb_fallback\
   ```

   32 位 `vcruntime140.dll` 和 `msvcp140.dll` 从 `C:\Windows\SysWOW64\` 复制,确保在精简版 Windows 上 adb 也能运行。

2. **执行打包**

   ```powershell
   .venv\Scripts\python -m pip install pyinstaller
   .venv\Scripts\pyinstaller --noconfirm SyncFiles.spec
   ```

3. **产物**:`dist\SyncFiles.exe`(约 16 MB),单文件,直接拷贝给最终用户即可。

### adb 解析顺序

`syncfiles.adb.resolve_adb_path` 在运行时按以下顺序探测 adb:

1. `SYNCFILES_ADB` 环境变量(高级用户的逃生口)
2. `PATH` 中的 `adb`(系统/用户安装的 platform-tools)
3. 与可执行文件同目录的 `adb.exe`(便携式覆盖)
4. 内置 fallback(打进 exe 的那一份)

### 已知限制

- **未签名 exe 首次运行会被 Windows SmartScreen 拦截**,需点击"更多信息"→"仍要运行"。要消除此提示需购买代码签名证书。
- **32 位 Windows 不支持**。本项目仅在 Windows 10/11 64 位上验证。
- adb 是 32 位程序,在 64 位 Windows 上通过 WoW64 运行,需目标机器已安装 32 位 VC++ 运行时(本 exe 已自带)。

## 基本使用流程

1. 使用手机同步模式时,先用 USB 连接 Android 手机
2. 通过 `python -m syncfiles`(开发模式)或双击 `SyncFiles.exe`(打包模式)打开应用
3. 如需切换语言,在顶部下拉框中选择 `中文` 或 `English`
4. 选择同步模式(硬盘模式 / 手机模式)
5. 硬盘模式下,选择左侧和右侧硬盘文件夹
6. 手机模式下,先点 **检查设备**,然后选择硬盘文件夹,从 `/sdcard` 浏览手机文件夹
7. 点击 **扫描** 查看差异
8. 双击冲突项,选择保留方式
9. 确认预览无误后,点击 **开始同步**

## 目录结构

```text
src/syncfiles/
  __main__.py            # 入口
  app.py                 # Tkinter UI 与事件循环
  adb.py                 # adb 进程封装 + 解析器
  domain.py              # 纯领域类型(计划、冲突、操作)
  executor.py            # 同步操作调度
  local_executor.py      # 硬盘-硬盘执行器
  local_fs.py            # 本地文件系统工具
  progress.py            # 进度报告(线程安全)
  i18n.py                # 中英文翻译
  adb_fallback/          # adb 二进制(构建时填充,不入库)

tests/                   # pytest 测试
SyncFiles.spec           # PyInstaller 配置
```

## 测试

```powershell
python -m pytest
```

测试套件覆盖 adb 客户端、领域逻辑、UI 控件、进度模型等。`tests/test_adb.py` 使用 `FakeRunner` 隔离真实 adb 进程,无需连接真机。
