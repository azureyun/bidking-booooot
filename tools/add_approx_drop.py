# -*- coding: utf-8 -*-
"""
为 ``Drop.csv`` 未收录的新物品生成"价格模仿"掉落边，并回写 bot 派生表。

背景
----
官方 ``Drop.csv``（掉落图）没有随客户端同步到最新物品池时，``item_prices.csv``
里会出现一批官方掉落图不引用的新物品。这类物品在 bot 的地图递归权重/期望价推断中
"隐形"（候选概率被兜底成等权），出价质量会退化。

本工具用确定性启发式补全（**非官方数据，仅供过渡**，官方 Drop.csv 更新后应整链重跑覆盖）：

1. 读官方 ``Drop.csv`` 展开全部掉落边；
2. 读 ``item_prices.csv``，求官方图未引用的新物品；
3. 对每个新物品，**按标签逐一挑选旧锚点**（同品质；优先标签重合更多、形状相同、
   base_value 更接近），镜像锚点在官方图里的整套池挂靠（池 id、ref_type）；
4. 边权按 ``base_value`` 反比缩放（仿同池"越贵越稀有"规律），并夹在所在池
   已有物品权重的 [池最小权, 池最大权] 区间内，杜绝极端值；
5. 输出审计 CSV ``data/drop_approx_edges.csv``（含锚点与缩放依据），并把
   ``官方边 ∪ 近似边`` 回写：
   - ``data/drop_table_weights.csv``（四列）
   - ``data/calculator_data_merged.csv``（DROP 行重写，ITEM 等行保留）

用法（仓库根目录）：
    python tools\\add_approx_drop.py            # 写派生表
    python tools\\add_approx_drop.py --dry-run  # 只预览计划
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
for p in (SRC, ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from bidking.tools.game_tables import (  # noqa: E402
    load_drop_edges_from_csv,
    merge_calculator_drop_rows,
    write_drop_table_weights_csv,
)

DropEdge = Tuple[int, int, int, int]

DATA_DIR = ROOT / "data"
DROP_CSV = DATA_DIR / "Drop.csv"
ITEMS_CSV = DATA_DIR / "item_prices.csv"
WEIGHTS_OUT = DATA_DIR / "drop_table_weights.csv"
MERGED_CSV = DATA_DIR / "calculator_data_merged.csv"
APPROX_OUT = DATA_DIR / "drop_approx_edges.csv"

# 参与锚点匹配的标签集合；只有带这些标签的物品才会被镜像挂靠到掉落池。
#
# 101-110 : 棋盘藏品（掉落推断的主体，必须参与）
# 100     : 特殊档，参与
# 14      : 皮肤 —— 不参与。皮肤只影响收藏展示，不出现在拍卖棋盘上，
#           若给它造近似边，会白白占掉掉落池的概率份额、稀释藏品权重。
#           注意：官方 Drop.csv 里已引用的旧皮肤不受此影响（那是官方数据，原样保留）。
# 111     : 万象藏品 —— 不参与。理由见 tools/README 或 data/更新数据白话版.md：
#           111 是全新类别，官方掉落图里一件旧锚点都没有，按现有"锚点须同标签"
#           约束必然匹配不上；且它出现概率低、价格约为同品质普通藏品的一半，
#           经评估按"不存在"处理，代价是地图期望价被略微高估（约 5%~10%）。
#           待官方 Drop.csv 更新或拿到实际爆率观测后再回来补。
AUCTION_TAGS = frozenset(range(101, 111)) | {100}


def load_items(path: Path) -> Dict[int, dict]:
    items: Dict[int, dict] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                iid = int(row["item_id"])
                tags = json.loads((row.get("category_tags") or "[]").strip())
                items[iid] = {
                    "name": row.get("name", ""),
                    "tags": [int(t) for t in tags],
                    "quality": int(row["quality"]),
                    "shape": int(row["shape"]),
                    "base_value": int(row["base_value"]),
                }
            except (KeyError, ValueError, TypeError):
                continue
    return items


def _log_price_ratio(ba: int, bb: int) -> float:
    """价格差距（对数尺度），价格越接近越小。"""
    if ba <= 0 or bb <= 0:
        return 0.0
    return abs(math.log(ba) - math.log(bb))


def _anchor_score(new: dict, old: dict) -> Optional[Tuple]:
    """分数越小越好：先比标签重合，再比形状，最后比价格对数距离。"""
    overlap = len(set(new["tags"]) & set(old["tags"]))
    if overlap <= 0:
        return None
    shape_mismatch = 1 if old["shape"] != new["shape"] else 0
    return (
        -overlap,
        shape_mismatch,
        _log_price_ratio(new["base_value"], old["base_value"]),
    )


def pick_anchors(
    new_items: List[int],
    items: Dict[int, dict],
    item_to_tags: Dict[int, List[int]],
    quality_pool: Dict[int, List[int]],
    tag_old_ids: Dict[int, List[int]],
) -> Dict[int, List[Tuple[int, int, float]]]:
    """
    返回 new_item_id -> [(tag, anchor_id, anchor_base), ...]。
    每个出现在拍卖标签里的 tag 各挑一个最优锚点：优先同品质；
    同品质无候选时放宽到任意品质的同标签旧物品（DFS 掉落权重与品质无关）。
    """
    new_by_id = {iid: items[iid] for iid in new_items}
    anchors: Dict[int, List[Tuple[int, int, float]]] = {}
    for iid, new in new_by_id.items():
        new_tag_set = set(new["tags"])
        chosen: List[Tuple[int, int, float]] = []
        seen_tags: Set[int] = set()
        for tag in new["tags"]:
            if tag not in AUCTION_TAGS or tag in seen_tags:
                continue
            seen_tags.add(tag)
            # 阶段：同品质+标签子集 → 同品质 → 任意品质+标签子集 → 任意品质。
            # "标签子集"指锚点标签 ⊆ 新物品标签，镜像出的挂靠才不会把新物品带进
            # 锚点独有（新物品没有）的类别池。
            stages = [
                (True, True),
                (True, False),
                (False, True),
                (False, False),
            ]
            best: Optional[Tuple] = None
            best_oid: Optional[int] = None
            for same_q, subset_only in stages:
                pool = (
                    quality_pool.get(new["quality"], ())
                    if same_q
                    else tag_old_ids.get(tag, ())
                )
                for oid in sorted(pool):
                    if oid in new_by_id:
                        continue
                    if tag not in item_to_tags.get(oid, []):
                        continue
                    if subset_only and not set(item_to_tags[oid]) <= new_tag_set:
                        continue
                    sc = _anchor_score(new, items[oid])
                    if sc is None:
                        continue
                    if best is None or sc < best:
                        best = sc
                        best_oid = oid
                if best_oid is not None:
                    break
            if best_oid is not None:
                chosen.append((tag, best_oid, items[best_oid]["base_value"]))
        anchors[iid] = chosen
    return anchors


def mirror_edges(
    new_items: List[int],
    items: Dict[int, dict],
    anchors: Dict[int, List[Tuple[int, int, float]]],
    official_edges: List[DropEdge],
    pool_child_edges: Dict[int, List[DropEdge]],
) -> Tuple[List[dict], Dict[int, Set[int]]]:
    """镜像锚点挂靠并缩放权重，返回 (审计行, item_id -> 可达根集合)。"""
    # 官方池内物品权重上限（仅统计官方引用到的物品边），近似权只封顶不抬底
    pool_hi: Dict[int, int] = {}
    for drop_id, ref_id, weight, _ref_type in official_edges:
        if ref_id not in items:
            continue
        pool_hi[drop_id] = max(pool_hi.get(drop_id, 0), weight)

    audit: List[dict] = []
    added_edges: List[DropEdge] = []
    for iid in new_items:
        new = items[iid]
        new_base = new["base_value"]
        seen_pools: Set[int] = set()
        for tag, anchor_id, _anchor_base in anchors.get(iid, []):
            anchor = items[anchor_id]
            for drop_id, _ref, weight, ref_type in pool_child_edges.get(anchor_id, ()):
                if drop_id in seen_pools:
                    continue
                seen_pools.add(drop_id)
                w = weight
                if new_base > 0 and anchor["base_value"] > 0:
                    # 价格反比缩放：新物品比锚点贵 → 权重低（更稀有）
                    w = int(round(weight * anchor["base_value"] / new_base))
                hi = pool_hi.get(drop_id)
                if hi is not None:
                    w = min(hi, w)
                w = max(1, int(w))
                added_edges.append((drop_id, iid, w, ref_type))
                audit.append(
                    {
                        "item_id": iid,
                        "item_name": new["name"],
                        "tags": new["tags"],
                        "quality": new["quality"],
                        "shape": new["shape"],
                        "base_value": new_base,
                        "drop_id": drop_id,
                        "weight": w,
                        "ref_type": ref_type,
                        "anchor_item_id": anchor_id,
                        "anchor_name": anchor["name"],
                        "anchor_base_value": anchor["base_value"],
                        "anchor_weight": weight,
                    }
                )
    # 可达根集合（官方图 + 近似边）
    parents: Dict[int, Set[int]] = defaultdict(set)
    for drop_id, ref_id, _w, _rt in official_edges + added_edges:
        parents[ref_id].add(drop_id)
    all_groups = {drop_id for drop_id, _r, _w, _rt in official_edges + added_edges}
    roots = {g for g in all_groups if not parents[g]}

    def reachable_roots(start: int) -> Set[int]:
        # 反向从 item 出发沿父链走到根
        out: Set[int] = set()
        stack = [start]
        seen: Set[int] = set()
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            for p in parents.get(node, ()):
                if p in roots:
                    out.add(p)
                elif p in all_groups:
                    stack.append(p)
        return out

    reach: Dict[int, Set[int]] = {iid: reachable_roots(iid) for iid in new_items}
    return audit, reach


def write_audit_csv(path: Path, rows: List[dict]) -> None:
    cols = [
        "item_id", "item_name", "tags", "quality", "shape", "base_value",
        "drop_id", "weight", "ref_type", "anchor_item_id", "anchor_name",
        "anchor_base_value", "anchor_weight",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: json.dumps(r[c], ensure_ascii=False) if isinstance(r[c], list) else r[c] for c in cols})


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--drop-csv", type=Path, default=DROP_CSV)
    ap.add_argument("--items-csv", type=Path, default=ITEMS_CSV)
    ap.add_argument("--weights-out", type=Path, default=WEIGHTS_OUT)
    ap.add_argument("--merged-csv", type=Path, default=MERGED_CSV)
    ap.add_argument("--approx-out", type=Path, default=APPROX_OUT)
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not args.drop_csv.is_file() or not args.items_csv.is_file():
        print("错误：需要 Drop.csv 与 item_prices.csv", file=sys.stderr)
        return 2
    if not args.merged_csv.is_file():
        print("错误：未找到 calculator_data_merged.csv", file=sys.stderr)
        return 2

    official = sorted(set(load_drop_edges_from_csv(args.drop_csv)))
    official_refs = {ref_id for _d, ref_id, _w, _rt in official}
    items = load_items(args.items_csv)
    new_items = sorted(set(items) - official_refs)
    print(f"官方 DROP 边 {len(official)} 条；item_prices {len(items)} 件；新物品 {len(new_items)} 件",
          file=sys.stderr)
    if not new_items:
        print("没有需要补全的新物品。", file=sys.stderr)
        return 0

    # 索引：旧物品 -> 作为子节点的池边；quality -> 同品质旧物品 id
    pool_child_edges: Dict[int, List[DropEdge]] = defaultdict(list)
    quality_pool: Dict[int, List[int]] = defaultdict(list)
    tag_old_ids: Dict[int, List[int]] = defaultdict(list)
    item_to_tags: Dict[int, List[int]] = {}
    for iid, meta in items.items():
        item_to_tags[iid] = meta["tags"]
        if iid not in official_refs:
            continue
        quality_pool[meta["quality"]].append(iid)
        for t in meta["tags"]:
            if t in AUCTION_TAGS:
                tag_old_ids[t].append(iid)
    for drop_id, ref_id, weight, ref_type in official:
        if ref_id in items:
            pool_child_edges[ref_id].append((drop_id, ref_id, weight, ref_type))

    anchors = pick_anchors(new_items, items, item_to_tags, quality_pool, tag_old_ids)
    no_anchor = [iid for iid in new_items if not anchors.get(iid)]
    if no_anchor:
        print("警告：以下新物品找不到任何旧锚点，将保持缺失：", file=sys.stderr)
        for iid in no_anchor:
            m = items[iid]
            print(f"  {iid} {m['name']} tags={m['tags']} q{m['quality']}", file=sys.stderr)

    audit, reach = mirror_edges(
        new_items, items, anchors, official, pool_child_edges
    )

    n_edges = len({(a["drop_id"], a["item_id"]) for a in audit})
    zero_reach = [iid for iid in new_items if not reach.get(iid)]
    print(f"近似边 {n_edges} 条（涉及 {len({a['item_id'] for a in audit})} 件新物品）；"
          f"不可达根的新物品 {len(zero_reach)} 件", file=sys.stderr)
    for iid in zero_reach:
        print(f"  !! 不可达 {iid} {items[iid]['name']}", file=sys.stderr)

    if args.dry_run:
        return 0

    # 审计 CSV
    if audit:
        write_audit_csv(args.approx_out, audit)
        print(f"已写入审计清单 {args.approx_out}（{len(audit)} 行）", file=sys.stderr)

    # 派生表：官方 ∪ 近似
    approx_edges: List[DropEdge] = [
        (a["drop_id"], a["item_id"], a["weight"], a["ref_type"]) for a in audit
    ]
    combined = sorted(set(official + approx_edges))
    write_drop_table_weights_csv(args.weights_out, combined)
    print(f"已写入 {args.weights_out}（{len(combined)} 条边）", file=sys.stderr)

    merged_tmp = args.merged_csv.with_name(args.merged_csv.stem + ".approx_tmp.csv")
    merge_calculator_drop_rows(args.merged_csv, combined, merged_tmp)
    os.replace(merged_tmp, args.merged_csv)
    print(f"已重写 {args.merged_csv}（DROP 行=官方∪近似，ITEM 行保留）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
