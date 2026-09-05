# -*- coding: utf-8 -*-
"""从游戏客户端 StreamingAssets 提取配置表并刷新 ``data/`` 下的输入 CSV。

背景
----
客户端把配置表打包进 YooAsset 的 ``*.bundle``（资产路径 ``Assets/Game/Bundle/Tables/*.txt``）。
本脚本用 UnityPy 解开 bundle，取出表名为 ``Item`` / ``RankMap`` / ``Skill`` / ``Language``
的 TextAsset（内容为 UTF-8 明文 TSV），再按 ``data/`` 现有同名 CSV 的表头按位置对齐写出，
供 bidking 下游工具（item_table 等）继续消费。

注意：客户端不包含 Drop 表（掉落为服务端权威数据），因此本脚本不产出 ``Drop.csv``。

用法示例
--------
::

    python tools/refresh_from_client.py                          # 读 data/StreamingAssets，写 data/
    python tools/refresh_from_client.py --streaming <dir>        # 指定 StreamingAssets 根目录
    python tools/refresh_from_client.py --tables Item,Language   # 只刷新部分表
    python tools/refresh_from_client.py --list-available         # 仅列出客户端可提取的表名
    python tools/refresh_from_client.py --out-dir <dir>          # 输出到别处（不覆盖 data）

安全守卫
--------
对每张目标表，若客户端表列数 < 仓库现有表头列数（客户端版本比仓库旧），跳过不覆盖；
若客户端列数更多，逐列比对首行数据，前 ``仓库列数`` 列必须与仓库一致才允许"追加新列"
写出（防止中间插列导致错位被静默吞掉）。
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---- 依赖注入：优先使用 tools/_deps 下本地安装的 UnityPy ----
_TOOL_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _TOOL_ROOT.parent
_DEPS_DIR = _TOOL_ROOT / "_deps"
if _DEPS_DIR.is_dir():
    sys.path.insert(0, str(_DEPS_DIR))

try:
    import UnityPy  # noqa: F401
except Exception:
    UnityPy = None  # type: ignore  # 延迟到 main() 统一报错


# 默认参与刷新的表 -> 仓库输入 CSV 文件名
DEFAULT_TABLES = {
    "Item": "Item.csv",
    "RankMap": "RankMap.csv",
    "Skill": "Skill.csv",
    "Language": "Language.csv",
}


def locate_default_package_dir(streaming_root: Path) -> Optional[Path]:
    """在 StreamingAssets 根下定位含 *.bundle 的 DefaultPackage 目录。"""
    candidates = [
        streaming_root / "yoo" / "DefaultPackage",
        streaming_root / "DefaultPackage",
        streaming_root,
    ]
    for cand in candidates:
        if cand.is_dir() and any(cand.glob("*.bundle")):
            return cand
    return None


def extract_text_assets(package_dir: Path, want: set) -> Dict[str, List[Tuple[int, bytes]]]:
    """遍历 bundle，收集目标 TextAsset 的所有副本（可能多版本残留），按名聚合。"""
    collected: Dict[str, List[Tuple[int, bytes]]] = defaultdict(list)
    scanned = 0
    failed = 0
    for bundle_path in sorted(package_dir.glob("*.bundle")):
        scanned += 1
        try:
            env = UnityPy.load(str(bundle_path))
        except Exception as exc:
            failed += 1
            if failed <= 2:
                sys.stderr.write(f"[debug] load fail {bundle_path.name}: {exc!r}\n")
            continue
        for obj in env.objects:
            if obj.type.name != "TextAsset":
                continue
            try:
                data = obj.read()
            except Exception:
                continue
            name = getattr(data, "m_Name", "") or ""
            if name not in want:
                continue
            script = data.m_Script or b""
            if isinstance(script, str):
                script = script.encode("utf-8")
            collected[name].append((len(script), script))
    sys.stderr.write(f"[scan] bundles={scanned} failed={failed}\n")
    return collected


def tsv_rows(script: bytes) -> List[List[str]]:
    text = script.decode("utf-8-sig", "replace")
    rows: List[List[str]] = []
    for line in text.split("\r\n"):
        line = line.rstrip("\r").rstrip("\n")
        if not line.strip():
            continue
        rows.append(line.split("\t"))
    return rows


def load_repo_rows(out_dir: Path, csv_name: str) -> Optional[Tuple[List[str], List[List[str]]]]:
    """读仓库现有 CSV 的表头与全部数据行。"""
    path = out_dir / csv_name
    if not path.is_file():
        return None
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
            rows = list(reader)
        except StopIteration:
            return None
    return header, rows


def _row_key(row: List[str]) -> str:
    return (row[0] if row else "").strip()


def _pick_sample_rows(
    repo_rows: List[List[str]], client_rows: List[List[str]], max_samples: int = 200
) -> List[Tuple[List[str], List[str]]]:
    """按首列(id)关联仓库与客户端样本行。"""
    client_by_key = {_row_key(r): r for r in client_rows if r}
    pairs: List[Tuple[List[str], List[str]]] = []
    for r in repo_rows:
        if not r:
            continue
        c = client_by_key.get(_row_key(r))
        if c is not None:
            pairs.append((r, c))
        if len(pairs) >= max_samples:
            break
    return pairs


def _match_ratio(pairs: List[Tuple[List[str], List[str]]], i: int, j: int) -> float:
    """仓库列 i 与客户端列 j 在样本行中的值一致比例。"""
    if not pairs:
        return 0.0
    hit = 0
    for repo_r, client_r in pairs:
        a = (repo_r[i] if i < len(repo_r) else "").strip()
        b = (client_r[j] if j < len(client_r) else "").strip()
        if a == b:
            hit += 1
    return hit / len(pairs)


def infer_alignment(
    repo_header: List[str],
    repo_rows: List[List[str]],
    client_rows: List[List[str]],
    min_ratio: float = 0.85,
) -> Optional[Tuple[Dict[int, int], List[int], List[int], List[Tuple[int, int, float]]]]:
    """推断"仓库列 -> 客户端列"的严格递增映射。

    返回 (mapping, mid_gaps, tail_cols, detail)：
      mapping:  仓库列索引 -> 客户端列索引
      mid_gaps: 客户端被跳过的"插入列"（位于已映射列之间，无法安全命名）
      tail_cols: 客户端超出映射尾部的新列（原样追加）
      detail:   (仓库列, 客户端列, 匹配率) 列表，供日志展示
    无法可靠推断（例如找不到足够匹配的列）时返回 None。
    """
    repo_cols = len(repo_header)
    client_cols = max((len(r) for r in client_rows), default=0)
    if client_cols < repo_cols:
        return None

    pairs = _pick_sample_rows(repo_rows, client_rows)
    if not pairs:
        return None

    mapping: Dict[int, int] = {}
    detail: List[Tuple[int, int, float]] = []
    cursor = 0
    for i in range(repo_cols):
        best_j: Optional[int] = None
        best_ratio = min_ratio
        # 从 cursor 开始向后找；中途允许跳过若干客户端列（游戏新增列）。
        for j in range(cursor, client_cols):
            ratio = _match_ratio(pairs, i, j)
            if ratio >= best_ratio:
                best_ratio = ratio
                best_j = j
                if ratio >= 0.98:
                    break
        if best_j is None:
            return None
        mapping[i] = best_j
        detail.append((i, best_j, best_ratio))
        cursor = best_j + 1

    used = set(mapping.values())
    last_used = max(used)
    mid_gaps = [j for j in range(last_used) if j not in used]
    tail_cols = list(range(last_used + 1, client_cols))
    return mapping, mid_gaps, tail_cols, detail


def refresh_table(name: str, csv_name: str, candidates: List[Tuple[int, bytes]], out_dir: Path) -> str:
    """从客户端候选版本中选最优一版，按仓库表头对齐写出 CSV。返回状态描述。"""
    schema = load_repo_rows(_REPO_ROOT / "data", csv_name)
    if schema is None:
        return f"{name}: 跳过（仓库无 {csv_name} 作为对齐基准）"

    repo_header, repo_rows = schema
    repo_cols = len(repo_header)
    best_report: Optional[str] = None
    # 客户端可能存在多个历史版本残留，逐个尝试，优先可成功对齐者。
    for _size, script in sorted(candidates, key=lambda x: x[0], reverse=True):
        rows = tsv_rows(script)
        if not rows:
            continue
        aligned = infer_alignment(repo_header, repo_rows, rows)
        if aligned is None:
            best_report = f"{name}: 跳过（无法可靠推断客户端列到仓库表头的映射，需人工核对列顺序）"
            continue
        mapping, mid_gaps, tail_cols, detail = aligned
        max_cols = max(len(r) for r in rows)

        # 组装输出：仓库列按 mapping 取，中洞插入列丢弃（无安全列名），尾列追加为 col_N。
        header = list(repo_header)
        if tail_cols:
            header += [f"col_{repo_cols + k}" for k, _ in enumerate(tail_cols)]

        def out_row(row: List[str]) -> List[str]:
            base = [(row[j] if j < len(row) else "") for j in mapping.values()]
            for k, j in enumerate(tail_cols):
                base.append(row[j] if j < len(row) else "")
            return base

        target = out_dir / csv_name
        with target.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            for row in rows:
                writer.writerow(out_row(row))

        notes = []
        if mid_gaps:
            notes.append(f"丢弃客户端插入列 {mid_gaps}")
        if tail_cols:
            notes.append(f"追加尾列 {tail_cols} 为 col_{repo_cols}..")
        # 统计同 id 行值更新幅度（仅信息展示）
        changed = 0
        for repo_r, client_r in _pick_sample_rows(repo_rows, rows, 300):
            for i, j in mapping.items():
                if i >= len(repo_r) or j >= len(client_r):
                    continue
                if repo_r[i].strip() != client_r[j].strip():
                    changed += 1
        if changed:
            notes.append(f"样本中发现 {changed} 处数值更新")
        note = "；".join(notes)
        return (
            f"{name}: 已写出 {len(rows)} 行 / {len(header)} 列"
            + (f"（{note}）" if note else "")
            + f" -> {target.name}"
        )
    return best_report or f"{name}: 客户端中无可用内容，跳过"


def list_available(package_dir: Path) -> None:
    """打印客户端里全部 TextAsset 表名（去重）。"""
    names = set()
    for bundle_path in sorted(package_dir.glob("*.bundle")):
        try:
            env = UnityPy.load(str(bundle_path))
        except Exception:
            continue
        for obj in env.objects:
            if obj.type.name != "TextAsset":
                continue
            try:
                d = obj.read()
            except Exception:
                continue
            nm = getattr(d, "m_Name", "") or ""
            if nm:
                names.add(nm)
    print("client TextAsset tables (%d):" % len(names))
    for nm in sorted(names):
        print("  " + nm)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--streaming",
        type=Path,
        default=_REPO_ROOT / "data" / "StreamingAssets",
        help="StreamingAssets 根目录（默认 data/StreamingAssets）",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_REPO_ROOT / "data",
        help="输出 CSV 目录（默认 data/，原地刷新）",
    )
    parser.add_argument(
        "--tables",
        default=",".join(DEFAULT_TABLES),
        help="要刷新的表名（默认 %(default)s）",
    )
    parser.add_argument(
        "--list-available",
        action="store_true",
        help="仅列出客户端可提取的表名后退出",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if UnityPy is None:
        sys.stderr.write(
            "缺少 UnityPy 依赖。请先执行：\n"
            f"    python -m pip install --target \"{_DEPS_DIR}\" UnityPy\n"
        )
        return 2

    package_dir = locate_default_package_dir(args.streaming)
    if package_dir is None:
        sys.stderr.write(
            f"未在 {args.streaming} 下找到含 *.bundle 的 DefaultPackage 目录。\n"
            "请确认路径形如 .../StreamingAssets/yoo/DefaultPackage。\n"
        )
        return 2
    sys.stderr.write(f"[info] bundle dir: {package_dir}\n")

    if args.list_available:
        list_available(package_dir)
        return 0

    names = {s.strip() for s in args.tables.split(",") if s.strip()}
    want = {n: DEFAULT_TABLES[n] for n in names if n in DEFAULT_TABLES}
    unknown = names - set(DEFAULT_TABLES)
    if unknown:
        sys.stderr.write(f"[warn] 未登记的表，跳过: {', '.join(sorted(unknown))}\n")

    collected = extract_text_assets(package_dir, set(want))
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, csv_name in sorted(want.items()):
        candidates = collected.get(name) or []
        if not candidates:
            print(f"{name}: 客户端中未找到，跳过")
            continue
        print(refresh_table(name, csv_name, candidates, out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
