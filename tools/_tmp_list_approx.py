# -*- coding: utf-8 -*-
import csv, json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

refs = set()
with open("data/drop_table_weights.csv", encoding="utf-8-sig", newline="") as f:
    for r in csv.DictReader(f):
        refs.add(int(r["ref_id"]))

approx = {}
with open("data/drop_approx_edges.csv", encoding="utf-8-sig", newline="") as f:
    for r in csv.DictReader(f):
        approx.setdefault(int(r["item_id"]), r["item_name"])

items = {}
with open("data/item_prices.csv", encoding="utf-8-sig", newline="") as f:
    for r in csv.DictReader(f):
        items[int(r["item_id"])] = {
            "name": r["name"], "tags": json.loads(r["category_tags"]), "q": r["quality"],
        }

print("=== 近似边涉及的物品 %d 件（含各品质分布）===" % len(approx))
for iid in sorted(approx):
    m = items.get(iid, {})
    print("%s q%s  %s  tags=%s" % (iid, m.get("q"), approx[iid], m.get("tags")))

print()
print("=== 名字含活动特征的物品（含官方图已引用的）===")
kw = ["限时", "活动", "限定", "特典", "周年", "新年", "春节", "圣诞", "万圣",
      "夏日", "盛夏", "金秋", "秋日", "中秋", "端午", "元宵", "纪念", "赛季",
      "通行证", "战令", "福袋", "联名", "联动", "测试", "公测", "预约", "回归",
      "收藏家", "观赏鱼", "花卉", "茶话会", "嘉年华", "庆典"]
for iid, m in sorted(items.items()):
    nm = m["name"]
    hit = [k for k in kw if k in nm]
    if hit:
        mark = "官方" if iid in refs else ("近似" if iid in approx else "--")
        print("%s q%s  %s  %s tags=%s" % (iid, m["q"], nm, mark, m["tags"]))
