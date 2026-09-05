"""配置文件 / 数据资源的标准路径解析。

- 优先取环境变量 ``BIDKING_HOME``。
- 否则向上找包含 ``configs/`` 与 ``data/`` 的目录。
- 兜底取当前工作目录。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def _looks_like_root(p: Path) -> bool:
    return (p / "configs").is_dir() and (p / "data").is_dir()


def project_root() -> Path:
    env = os.environ.get("BIDKING_HOME")
    if env:
        return Path(env).resolve()

    if getattr(sys, "frozen", False):
        # 打包后的程序：以 exe 所在目录为起点向上找最近一棵含 configs/ + data/ 的树，
        # 让「exe + data + configs 同级分发」在任何启动方式（双击/命令行/工作目录）下都一致，
        # 不再受源码仓库位置或当前工作目录影响。
        start: Path = Path(sys.executable).resolve().parent
    else:
        # 源码运行：以本模块所在目录为起点向上找（仓库根/被 pip install 的站点均可）。
        start = Path(__file__).resolve().parent

    for parent in (start, *start.parents):
        if _looks_like_root(parent):
            return parent

    if getattr(sys, "frozen", False):
        # 冻结程序兜底：exe 同级即根（不存在 configs/data 时在其下新建，分发缺失文件不静默写丢）
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def configs_dir() -> Path:
    return project_root() / "configs"


def data_dir() -> Path:
    return project_root() / "data"


def board_snapshot_default_path() -> Path:
    """默认画板快照 JSON：``<project_root>/data/board_snapshot.json``。"""
    return (data_dir() / "board_snapshot.json").resolve()


def resolve_board_snapshot_path(raw: Optional[str]) -> Path:
    """
    解析 ``board_snapshot.path``（``configs`` 合并结果）。

    - 空或未设置：:func:`board_snapshot_default_path`。
    - 绝对路径：``expanduser`` 后 ``resolve``。
    - 相对路径：相对 :func:`project_root`（例如 ``data/board_snapshot.json`` 与 exe+data 分发一致）。
    """
    s = (raw or "").strip()
    if not s:
        return board_snapshot_default_path()
    p = Path(s).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (project_root() / s).resolve()


def runtime_path() -> Path:
    return configs_dir() / "runtime.json"


def config_overlay_path() -> Path:
    """与本机/玩法相关的覆盖配置（窗口、点击、快照路径等），与 :func:`runtime_path` 深合并。"""
    return configs_dir() / "config.json"


def pricing_path() -> Path:
    return configs_dir() / "pricing.json"


def pricing_map_overlay_path(map_id: int | str) -> Path:
    """``configs/pricing.maps/<map_id>.json`` 路径（文件不一定已存在，供 GUI 读写）。"""
    return configs_dir() / "pricing.maps" / f"{map_id}.json"


def pricing_map_override_path(map_id: int | str) -> Optional[Path]:
    candidate = pricing_map_overlay_path(map_id)
    return candidate if candidate.is_file() else None
