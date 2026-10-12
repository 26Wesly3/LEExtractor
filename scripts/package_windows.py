"""Build the portable Windows fallback package (v0.9.13, competition "plan B").

Produces a self-contained folder that a judge can run by double-clicking one
file, with no installer, no admin rights and no network access on stage:

    LEExtractor-Windows/
    ├── Start_LEExtractor.bat     <- the only file a user touches
    ├── app/                      <- this repository's source
    ├── runtime/                  <- optional portable Python (--runtime)
    ├── models/                   <- pre-installed translation/embedding models
    ├── data/  cache/  logs/      <- written at run time, survive upgrades
    └── README.txt

Usage::

    python scripts/package_windows.py                     # app + launcher only
    python scripts/package_windows.py --runtime C:\\Python312-portable
    python scripts/package_windows.py --models D:\\leextractor-models --zip

The launcher prefers ``runtime\\python.exe``, then a ``.venv`` next to it, then
the system ``python``; it sets every LEEXTRACTOR_* directory to the package's own
subfolders, so the portable copy never writes into the user's profile.
"""

from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "dist-windows"

#: Shipped as-is, relative to the repository root.
APP_FILES = (
    "pyproject.toml", "requirements.txt", "constraints.txt",
    "README.md", "README.zh-CN.md", "CHANGES.md", "ARCHITECTURE.md",
    "ROADMAP.md", "ONBOARDING.md", "DEPLOYMENT.md", ".env.example",
)
APP_DIRS = ("litsearch", "scripts", "web", "benchmarks")

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".sessions", "node_modules",
             "dist", ".git", "deliverables", "_workspace_tmp", ".web-data"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".db", ".log", ".zip", ".part")

LAUNCHER = r"""@echo off
rem LEExtractor portable launcher (v0.9.13). Keep this file CRLF.
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
title LEExtractor (portable)

set "LEEXTRACTOR_DATA_DIR=%~dp0data"
set "LEEXTRACTOR_MODEL_DIR=%~dp0models"
set "LEEXTRACTOR_CACHE_DIR=%~dp0cache"
set "LEEXTRACTOR_LOG_DIR=%~dp0logs"
set "LEEXTRACTOR_HOST=127.0.0.1"
set "LEEXTRACTOR_PORT=8012"
set "LEEXTRACTOR_LOG_FORMAT=text"
set "LEEXTRACTOR_MAX_ACTIVE_JOBS=1"

if not exist "data"  mkdir "data"
if not exist "cache" mkdir "cache"
if not exist "logs"  mkdir "logs"

set "PYEXE="
if exist "runtime\python.exe" set "PYEXE=runtime\python.exe"
if not defined PYEXE if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
if not defined PYEXE set "PYEXE=python"

%PYEXE% -c "import fastapi, uvicorn, requests" >nul 2>&1
if errorlevel 1 (
    echo [1/2] 首次运行：安装依赖（需要网络，几分钟）
    %PYEXE% -m pip install --disable-pip-version-check -e "app" -c "app\constraints.txt"
    if errorlevel 1 goto failed
)

echo [2/2] 启动服务并打开浏览器： http://127.0.0.1:8012
echo 关闭本窗口即停止服务。
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8012"
%PYEXE% -m litsearch.web
goto end

:failed
echo.
echo 启动失败：请确认已安装 Python 3.10 以上，或把便携版 Python 放到 runtime\ 目录。
pause

:end
endlocal
"""

README = """LEExtractor 便携版（Windows）
================================

双击 Start_LEExtractor.bat 即可：
  1. 启动后端（默认 http://127.0.0.1:8012）
  2. 自动打开浏览器
  3. 保持窗口打开即为运行中，关闭窗口即停止

目录说明
--------
  app\\      程序源码（只读即可）
  runtime\\  可选：便携版 Python（有则优先使用）
  models\\   翻译 / Embedding 模型（可提前放好，比赛现场无需联网下载）
  data\\     你的项目数据 —— 备份就备份这个目录
  cache\\    检索缓存（删掉只是变慢）
  logs\\     日志（自动轮转）

现场注意事项
------------
* 比赛演示优先用此本地版；云端版为 https://<子域>.duckdns.org（见 DEPLOYMENT.md）
* 没网也能用：模型已在 models\\ 里、结果可以离线复算
* 端口被占用时：set LEEXTRACTOR_PORT=8013 后重新双击
* 数据不丢：升级只需替换 app\\ 目录，data\\ 与 models\\ 原样保留
"""


def _ignore(_directory, names):
    return [name for name in names if name in SKIP_DIRS]


def _copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, ignore=_ignore, dirs_exist_ok=True)


def build(out_dir: Path, runtime: Path | None, models: Path | None, make_zip: bool) -> dict:
    package = out_dir / "LEExtractor-Windows"
    if package.exists():
        shutil.rmtree(package)
    app = package / "app"
    app.mkdir(parents=True)
    for name in APP_DIRS:
        source = ROOT / name
        if source.is_dir():
            _copy_tree(source, app / name)
    for name in APP_FILES:
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, app / name)
    for name in ("data", "cache", "logs", "models"):
        (package / name).mkdir(exist_ok=True)
    (app / ".env").write_text((ROOT / ".env.example").read_text(encoding="utf-8"),
                              encoding="utf-8")

    if models and models.is_dir():
        _copy_tree(models, package / "models")
    if runtime and runtime.is_dir():
        _copy_tree(runtime, package / "runtime")

    # CRLF is mandatory for .bat: cmd.exe mis-parses LF-only labels and comments.
    (package / "Start_LEExtractor.bat").write_bytes(LAUNCHER.replace("\n", "\r\n").encode("utf-8"))
    (package / "README.txt").write_text(README, encoding="utf-8")

    files = [p for p in package.rglob("*") if p.is_file()]
    archive = None
    if make_zip:
        archive = out_dir / "LEExtractor-Windows.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(files):
                zf.write(path, path.relative_to(out_dir).as_posix())
    return {
        "package": str(package),
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "runtime_bundled": bool(runtime and runtime.is_dir()),
        "models_bundled": bool(models and models.is_dir()),
        "zip": str(archive) if archive else None,
        "launcher_crlf": b"\r\n" in (package / "Start_LEExtractor.bat").read_bytes(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the portable Windows package")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--runtime", default="", help="portable Python directory to bundle")
    parser.add_argument("--models", default="", help="pre-downloaded models directory to bundle")
    parser.add_argument("--zip", action="store_true", help="also write LEExtractor-Windows.zip")
    args = parser.parse_args(argv)
    summary = build(Path(args.out), Path(args.runtime) if args.runtime else None,
                    Path(args.models) if args.models else None, args.zip)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
