"""一次性证据覆盖分析脚本（仅供评估，不落库）"""
import json
import os
from collections import Counter

SNAP = "snapshots"

PROJECTS = [d for d in os.listdir(SNAP) if os.path.isdir(os.path.join(SNAP, d))]

def load_json(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return None

def summarize(rounds):
    """从一个项目(或legacy根)的 {V1:data, V2:..} 提取视角汇总"""
    info = {}
    info["rounds"] = sorted(rounds.keys())
    info["question"] = (rounds.get("V1") or {}).get("question", "")
    # 评分曲线
    scores = {}
    for r in sorted(rounds.keys()):
        d = rounds.get(r) or {}
        os_ = d.get("overall_score", d.get("scores", {}).get("overall"))
        scores[f"{r}_score"] = round(os_, 2) if os_ else None
    info.update(scores)

    # evidence 覆盖（汇总所有轮的 evidence_list + source 分布）
    ev_count = 0
    src_counter = Counter()
    for r in rounds.values():
        exp = (r or {}).get("agent_explorer") or {}
        evs = exp.get("evidence_list") or []
        ev_count += len(evs)
        for e in evs:
            s = e.get("source") or ""
            # 截短 source 分类
            key = s if len(s) <= 30 else s[:30]
            src_counter[key] += 1
    info["total_evidence"] = ev_count
    info["distinct_sources"] = len(src_counter)
    info["top_sources"] = [f"{k}({v})" for k, v in src_counter.most_common(6)]
    return info

lines = []
# legacy 根
legacy = {}
for r in ("V1", "V2", "V3"):
    d = load_json(os.path.join(SNAP, f"{r}.json"))
    if d:
        legacy[r] = d
if legacy:
    s = summarize(legacy)
    lines.append(("LEGACY_ROOT", s))

for proj in PROJECTS:
    rounds = {}
    for f in os.listdir(os.path.join(SNAP, proj)):
        if f.endswith(".json") and f[:-5] in ("V1", "V2", "V3", "V4", "V5"):
            d = load_json(os.path.join(SNAP, proj, f))
            if d:
                rounds[f[:-5]] = d
    if not rounds:
        continue
    lines.append((proj, summarize(rounds)))

# 输出
print(f"{'###':3}{'项目':38}{'轮次':14}{'问题':34}{'评分':22}{'证据':6}{'源':4}  主要来源")
print("-" * 130)
for name, s in lines:
    rounds = ",".join(s["rounds"])
    q = (s["question"] or "")[:32]
    score = " ".join(f"{k}={v}" for k, v in s.items() if k.endswith("_score") and v)
    print(f"{name[:36]:38} {rounds:14} {q:34} {score:22} {s['total_evidence']:4} {s['distinct_sources']:3}  {', '.join(s['top_sources'])}")