# -*- coding: utf-8 -*-
"""把 dsh-pet-indesktop 部署成 D:\\大肥鱼 的绿色三段式布局。

```
D:\\大肥鱼\\
  program\\   程序（源码 + 进入脚本；升级时整目录替换）
  data\\      配置 / 会话 / 记忆 / 知识库 / 日志（DSH_PET_BASE 指向这里）
  assets\\    角色动画、音效
  启动.bat    双击运行
```

## 为什么要给 assets 建目录联接（junction）

程序按相对路径读 `program/assets`，而用户要求资源集中在 `大肥鱼\\assets`。
复制两份会白占 70MB 且升级后容易不同步，所以优先建 junction（Windows 上
普通用户对本地磁盘也能建，不需要管理员）；建失败则退回复制，并如实告知。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = Path(r"D:\大肥鱼")
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", ".mypy_cache", "build", "dist",
             "dist-onedir", "build-onedir", "_internal", ".venv", "node_modules",
             # assets 单独走 junction/copy，避免在 program 里再复制一份 70MB
             "assets"}

LAUNCH_BAT = """@echo off
chcp 65001 >nul
setlocal
rem 绿色运行：数据全部落在大肥鱼\\data，不写系统盘
set "DSH_PET_BASE=%~dp0data"
set "PYTHONUTF8=1"
cd /d "%~dp0program"
where pythonw >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python，请先安装 Python 3.10+ 并勾选 Add to PATH
  pause
  exit /b 1
)
start "" pythonw -m pet
endlocal
"""


def _copy_tree(src: Path, dst: Path, force: bool = False) -> tuple[int, int]:
    files = size = 0
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists() and not force:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(item, target)
        except OSError:
            continue
        files += 1
        size += target.stat().st_size
    return files, size


def _link_or_copy_assets(source: Path, target: Path) -> str:
    """优先建立目录联接，失败则复制。返回采用的策略名。"""
    if target.exists() or target.is_symlink():
        return "exists"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # /J 是目录联接，不需要管理员权限
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(source)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and target.exists():
            return "junction"
    except (OSError, subprocess.SubprocessError):
        pass
    _copy_tree(source, target)
    return "copy"


def deploy(target: Path = TARGET, force: bool = False) -> dict:
    program = target / "program"
    data = target / "data"
    assets = target / "assets"
    # 注意：不预先创建 assets 目录，否则 junction 无法建立（mklink 要求目标不存在）
    for d in (program, data):
        d.mkdir(parents=True, exist_ok=True)

    stats: dict[str, object] = {}

    # 1) 程序：整份源码（跳过构建产物与缓存）
    files, size = _copy_tree(ROOT, program, force=force)
    stats["program"] = (files, size)

    # 2) 资源：junction 优先
    stats["assets"] = (_link_or_copy_assets(ROOT / "assets", assets), 0)

    # 3) 数据目录骨架（DSH_PET_BASE 会指向 data，程序自己在下面建 dsh-pet-standalone）
    for name in ("sessions", "kb", "logs"):
        (data / name).mkdir(parents=True, exist_ok=True)

    # 4) 启动脚本
    (target / "启动.bat").write_text(LAUNCH_BAT, encoding="utf-8")
    (target / "README.txt").write_text(
        "AI大肥鱼（dsh-pet-indesktop）绿色部署\r\n"
        "\r\n"
        "双击 启动.bat 运行。\r\n"
        "  program\\  程序源码（升级时整目录替换）\r\n"
        "  data\\     配置、会话、记忆、知识库、日志（不写系统盘）\r\n"
        "  assets\\   角色动画与音效\r\n"
        "\r\n"
        "卸载：直接删除整个 大肥鱼 目录即可，系统盘无残留。\r\n",
        encoding="utf-8")
    return stats


def verify(target: Path = TARGET) -> dict:
    out = {}
    for name in ("program", "data", "assets"):
        p = target / name
        files = [x for x in p.rglob("*") if x.is_file()] if p.exists() else []
        out[name] = {"files": len(files),
                     "mb": round(sum(x.stat().st_size for x in files) / 1024 / 1024, 1)}
    out["entry"] = {
        "pet_main": (target / "program" / "pet" / "__main__.py").exists(),
        "launch_bat": (target / "启动.bat").exists(),
        "characters": (target / "assets" / "characters").is_dir(),
        "assets_is_junction": os.path.islink(str(target / "assets")),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=str(TARGET))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    target = Path(args.target)

    print(f"部署到 {target} …")
    stats = deploy(target, force=args.force)
    print(f"  program  {stats['program'][0]:5} 个文件  {stats['program'][1] / 1024 / 1024:7.1f} MB")
    print(f"  assets   方式 = {stats['assets']}")

    info = verify(target)
    print("\n校验：")
    for name in ("program", "data", "assets"):
        print(f"  {name:8} {info[name]['files']:5} 个文件  {info[name]['mb']:7.1f} MB")
    print("  入口:", info["entry"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
