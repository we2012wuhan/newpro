# -*- coding: utf-8 -*-
"""
贝叶斯日记 · 计算内核
=====================
这个模块只做数学：不碰数据库、不碰网络、不依赖 FastAPI，可以单独跑、单独验。

它负责三件事：
  1. 似然比（LR）与后验更新 —— 一条证据到底把你的信念推动了多少
  2. 结算之后的校准评估 —— Brier / 对数损失 / ECE / AUC / Murphy 分解
  3. 把上面这些数字翻译成人话 —— insights，告诉你"你错在哪一类事上"

为什么要有这个模块：贝叶斯的价值不在于算出一个漂亮的后验，
而在于让你看见自己的先验和证据各自有多靠谱。所以这里的输出全部
围绕"对比"：先验 vs 后验、你说 vs 实际、早期 vs 最近。
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------
# 边界处理
# ---------------------------------------------------------------
# 概率的"地板"：用户给 0% 或 100% 时，数学上会出现 log(0) / 除以 0，
# 而且真实世界里也不存在绝对确定。统一压到 [EPS, 1-EPS]。
EPS = 0.01
LR_MIN = 1.0 / 100.0
LR_MAX = 100.0
P_FLOOR = 1e-6


def clamp(x: float, lo: float, hi: float) -> float:
    """把任何输入压进 [lo, hi]。

    这里顺手做了类型兜底：上游可能传来 None / 字符串 / NaN，
    数学层不该因为一个脏值就抛异常，压到边界继续算更安全。
    """
    try:
        x = float(x)
    except (TypeError, ValueError):
        return lo
    if x != x:  # NaN
        return lo
    return lo if x < lo else (hi if x > hi else x)


# ---------------------------------------------------------------
# 一、信念更新
# ---------------------------------------------------------------
def to_odds(p: float) -> float:
    """概率 -> 胜算（odds）。0.75 -> 3，意思是"是的三份对不是的一份"。"""
    q = clamp(p, EPS, 1.0 - EPS)
    return q / (1.0 - q)


def from_odds(o: float) -> float:
    """胜算 -> 概率。贝叶斯更新的全部秘密就是：胜算可以直接相乘。"""
    if o <= 0:
        return 0.0
    if math.isinf(o):
        return 1.0
    return o / (1.0 + o)


def lr_from_pair(p_if_true: float, p_if_false: float) -> float:
    """由两个可能性算出似然比。

    p_if_true  : 如果这件事真的会发生，我看到这条证据的可能性有多大
    p_if_false : 如果这件事不会发生，我看到它的可能性有多大
    LR = p_if_true / p_if_false

    LR > 1 支持它发生，LR < 1 支持它不发生，LR = 1 等于白说。
    """
    a = clamp(p_if_true, EPS, 1.0)
    b = clamp(p_if_false, EPS, 1.0)
    return clamp(a / b, LR_MIN, LR_MAX)


def posterior(prior: float, lrs) -> float:
    """先验 + 一串似然比 -> 后验。"""
    o = to_odds(prior)
    for lr in lrs:
        o *= clamp(lr, LR_MIN, LR_MAX)
    return clamp(from_odds(o), 0.0, 1.0)


def step_posteriors(prior: float, lrs):
    """逐步后验：用来画"每加一条证据，概率走到哪"。"""
    o = to_odds(prior)
    out = [clamp(prior, 0.0, 1.0)]
    for lr in lrs:
        o *= clamp(lr, LR_MIN, LR_MAX)
        out.append(clamp(from_odds(o), 0.0, 1.0))
    return out


def sequence_demo(prior: float, lrs_a, lrs_b) -> dict:
    """顺序不改变结论：先 A 后 B 与先 B 后 A，终点必然相同。

    这不是巧合，是乘法的交换律 —— 而贝叶斯更新本质上只是乘法。
    """
    path1 = step_posteriors(prior, list(lrs_a) + list(lrs_b))
    path2 = step_posteriors(prior, list(lrs_b) + list(lrs_a))
    return {
        "path1": path1,
        "path2": path2,
        "final": path1[-1],
        "identical": abs(path1[-1] - path2[-1]) < 1e-9,
    }


# ---------------------------------------------------------------
# 二、校准评估：你嘴上说的概率，和你实际的表现差多远
# ---------------------------------------------------------------
def _clean(pairs):
    out = []
    for p, y in pairs:
        try:
            pf = clamp(float(p), 0.0, 1.0)
            yf = 1.0 if float(y) >= 0.5 else 0.0
        except (TypeError, ValueError):
            continue
        out.append((pf, yf))
    return out


def brier_score(pairs):
    """Brier 分数：平均的 (预测 - 实际)^2。0 最好，0.25 相当于永远说 50%。"""
    ps = _clean(pairs)
    if not ps:
        return None
    return sum((p - y) ** 2 for p, y in ps) / len(ps)


def log_loss(pairs):
    """对数损失：对"自信地错"惩罚极重。永远说 50% 大约是 0.693。"""
    ps = _clean(pairs)
    if not ps:
        return None
    total = 0.0
    for p, y in ps:
        q = clamp(p, P_FLOOR, 1.0 - P_FLOOR)
        total -= math.log(q if y == 1.0 else 1.0 - q)
    return total / len(ps)


def reliability_buckets(pairs, bins: int = 10):
    """可靠性曲线的分桶数据：每一档"你说多少"对应"实际多少"。"""
    ps = _clean(pairs)
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        grp = [(p, y) for p, y in ps if (lo <= p < hi) or (i == bins - 1 and p == hi)]
        if not grp:
            out.append({"lo": lo, "hi": hi, "n": 0, "mean_pred": None, "mean_outcome": None})
            continue
        out.append({
            "lo": lo,
            "hi": hi,
            "n": len(grp),
            "mean_pred": sum(p for p, _ in grp) / len(grp),
            "mean_outcome": sum(y for _, y in grp) / len(grp),
        })
    return out


def expected_calibration_error(pairs, bins: int = 10):
    """ECE：把每一档的偏差按样本量加权平均。比 Brier 更直白 ——
    它就是"平均而言你偏离真实概率几个百分点"。"""
    ps = _clean(pairs)
    if not ps:
        return None
    total = 0.0
    for b in reliability_buckets(ps, bins):
        if b["n"]:
            total += b["n"] * abs(b["mean_pred"] - b["mean_outcome"])
    return total / len(ps)


def auc_score(pairs):
    """AUC：你有没有把"会发生的"和"不会发生的"分开。

    0.5 = 和抛硬币一样（等于没判断），1.0 = 完全分得开。
    注意它和校准是两件事：你可以很会排序，但整体偏高。
    """
    ps = _clean(pairs)
    pos = [p for p, y in ps if y == 1.0]
    neg = [p for p, y in ps if y == 0.0]
    if not pos or not neg:
        return None
    wins = 0.0
    for a in pos:
        for b in neg:
            if a > b:
                wins += 1.0
            elif a == b:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def murphy_decomposition(pairs, bins: int = 10):
    """Brier 的三项分解（分桶近似）：

        Brier ≈ 可靠性 - 分辨力 + 不确定性

      可靠性（reliability）：校准误差，越小越好 —— 你说 70% 就真该发生 70%
      分辨力（resolution） ：你能不能把不同结果的人区分开，越大越好
      不确定性（uncertainty）：这件事本身有多难猜，是数据决定的，你改不了

    看这三项就知道该练什么：可靠性差 = 练校准；分辨力低 = 练判断依据。
    """
    ps = _clean(pairs)
    if not ps:
        return None
    n = len(ps)
    base = sum(y for _, y in ps) / n
    rel = res = 0.0
    for b in reliability_buckets(ps, bins):
        if not b["n"]:
            continue
        w = b["n"] / n
        rel += w * (b["mean_pred"] - b["mean_outcome"]) ** 2
        res += w * (b["mean_outcome"] - base) ** 2
    unc = base * (1.0 - base)
    return {"reliability": rel, "resolution": res, "uncertainty": unc}


def summarize(pairs) -> dict:
    """把一组 (预测概率, 实际结果) 压成一份体检报告。"""
    ps = _clean(pairs)
    n = len(ps)
    if n == 0:
        return {"n": 0}
    base = sum(y for _, y in ps) / n
    mean_pred = sum(p for p, _ in ps) / n
    var = sum((p - mean_pred) ** 2 for p, _ in ps) / n
    return {
        "n": n,
        "base_rate": base,
        "mean_pred": mean_pred,
        "bias": mean_pred - base,
        "sharpness": math.sqrt(var),
        "brier": brier_score(ps),
        "log_loss": log_loss(ps),
        "ece": expected_calibration_error(ps),
        "auc": auc_score(ps),
        "murphy": murphy_decomposition(ps),
    }


# ---------------------------------------------------------------
# 三、把数字翻译成人话
# ---------------------------------------------------------------
def _pair_logloss(p: float, y: float) -> float:
    q = clamp(p, P_FLOOR, 1.0 - P_FLOOR)
    return -math.log(q if y >= 0.5 else 1.0 - q)


def _pp(x: float, digits: int = 0) -> str:
    return ("%." + str(digits) + "f%%") % (100.0 * x)


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def _domain_table(records):
    groups = {}
    for r in records:
        key = (r.get("domain") or "未分类").strip() or "未分类"
        groups.setdefault(key, []).append(r)
    rows = []
    for name, rs in groups.items():
        s = summarize([(r["posterior"], r["outcome"]) for r in rs])
        rows.append({
            "domain": name,
            "n": s["n"],
            "mean_pred": s["mean_pred"],
            "base_rate": s["base_rate"],
            "bias": s["bias"],
            "brier": s["brier"],
        })
    rows.sort(key=lambda x: (-x["n"], x["domain"]))
    return rows


def _evidence_table(records):
    """证据到底帮了忙还是帮了倒忙。

    对每条结算记录分别算：只用先验的对数损失 vs 用上证据之后的对数损失。
    后者更小 = 证据把你带对了方向；更大 = 你被证据带偏了。
    这是"我到底会不会搜集证据"的唯一客观答案。
    """
    used = [r for r in records if r.get("n_evidence")]
    if not used:
        return {"n_bets": 0, "n_evidence": 0, "helped": 0, "hurt": 0,
                "neutral": 0, "mean_abs_move": 0.0}
    helped = hurt = neutral = 0
    for r in used:
        before = _pair_logloss(r["prior"], r["outcome"])
        after = _pair_logloss(r["posterior"], r["outcome"])
        if after < before - 1e-9:
            helped += 1
        elif after > before + 1e-9:
            hurt += 1
        else:
            neutral += 1
    moves = [abs(r["posterior"] - r["prior"]) for r in used]
    return {
        "n_bets": len(used),
        "n_evidence": sum(int(r.get("n_evidence") or 0) for r in used),
        "helped": helped,
        "hurt": hurt,
        "neutral": neutral,
        "mean_abs_move": _mean(moves) or 0.0,
    }


def _drift_table(records):
    """早期 vs 最近的偏差，用来看你在收敛还是在变随意。"""
    if len(records) < 6:
        return None
    ordered = sorted(records, key=lambda r: str(r.get("settled_at") or r.get("created_at") or ""))
    half = len(ordered) // 2
    early, late = ordered[:half], ordered[half:]
    if len(early) < 3 or len(late) < 3:
        return None
    e = summarize([(r["posterior"], r["outcome"]) for r in early])
    l = summarize([(r["posterior"], r["outcome"]) for r in late])
    return {
        "early_n": e["n"], "early_bias": e["bias"],
        "late_n": l["n"], "late_bias": l["bias"],
        "improving": abs(l["bias"]) < abs(e["bias"]) - 0.05,
        "worsening": abs(l["bias"]) > abs(e["bias"]) + 0.08,
    }


def normalize_records(records):
    """把外部传进来的记录洗干净。

    这里挡三类脏东西：看不到结果的、概率为空的、证据数是字符串的。
    洗不干净的直接丢掉 —— 一份报告少算一条，好过整页报错。
    """
    out = []
    for r in records or []:
        if not isinstance(r, dict):
            continue
        try:
            y = int(r.get("outcome"))
        except (TypeError, ValueError):
            continue
        if y not in (0, 1):
            continue
        try:
            ne = int(r.get("n_evidence") or 0)
        except (TypeError, ValueError):
            ne = 0
        out.append({
            "prior": clamp(r.get("prior"), 0.0, 1.0),
            "posterior": clamp(r.get("posterior"), 0.0, 1.0),
            "outcome": y,
            "domain": str(r.get("domain") or "").strip(),
            "created_at": str(r.get("created_at") or ""),
            "settled_at": str(r.get("settled_at") or r.get("created_at") or ""),
            "n_evidence": max(0, ne),
        })
    return out


def _insight(level: str, title: str, text: str) -> dict:
    return {"level": level, "title": title, "text": text}


def analyze(records, open_count: int = 0) -> dict:
    """records: 记录列表，每条包含
        prior      下注时写下的概率（锁定，事后不可改）
        posterior  结算那一刻的后验（没记证据时等于 prior）
        outcome    1 = 发生了，0 = 没发生
        domain     领域标签
        created_at / settled_at   ISO 字符串
        n_evidence 这条预测一共记了几条证据
    返回：体检报告 + 一堆用真实数字写成的提醒。
    """
    settled = normalize_records(records)
    try:
        open_count = int(open_count or 0)
    except (TypeError, ValueError):
        open_count = 0
    n = len(settled)
    out = {
        "counts": {"settled": n, "open": int(open_count)},
        "overall": summarize([]),
        "baseline_prior": summarize([]),
        "reliability": [],
        "domains": [],
        "evidence": _evidence_table([]),
        "drift": None,
        "insights": [],
    }
    if n == 0:
        out["insights"].append(_insight(
            "info", "先攒 5 条结果",
            "校准这件事没法空谈。写 5 条能在一周内验证的预测，结算之后再回来看这一页。"))
        return out

    pairs_post = [(r["posterior"], r["outcome"]) for r in settled]
    pairs_prior = [(r["prior"], r["outcome"]) for r in settled]
    overall = summarize(pairs_post)
    baseline = summarize(pairs_prior)
    domains = _domain_table(settled)
    ev = _evidence_table(settled)
    drift = _drift_table(settled)

    out.update({
        "overall": overall,
        "baseline_prior": baseline,
        "reliability": reliability_buckets(pairs_post),
        "domains": domains,
        "evidence": ev,
        "drift": drift,
    })
    ins = out["insights"]

    # --- 样本量先声明 ---
    if n < 5:
        ins.append(_insight(
            "info", "样本只有 %d 条" % n,
            "下面的数字先当成提示，不要当结论。校准曲线至少要 20 条才有形状。"))

    # --- 整体偏差 ---
    gap = overall["bias"]
    if gap >= 0.10:
        ins.append(_insight(
            "warn", "你在系统性高估",
            "你平均给自己 %s 的把握，实际只发生了 %s —— 高估了 %.0f 个百分点。"
            "下次写下概率时，先在心里减掉 %.0f。" % (
                _pp(overall["mean_pred"]), _pp(overall["base_rate"]),
                gap * 100, gap * 100)))
    elif gap <= -0.10:
        ins.append(_insight(
            "warn", "你在系统性低估",
            "你平均只给 %s 的把握，实际发生了 %s —— 你比自己以为的更有把握。"
            "该说的话可以再肯定一点。" % (_pp(overall["mean_pred"]), _pp(overall["base_rate"]))))
    elif n >= 8 and overall["sharpness"] >= 0.06:
        ins.append(_insight(
            "good", "整体是准的",
            "你平均报 %s，实际发生 %s，只差 %.0f 个百分点。"
            "这说明你对自己有多大把握，心里是有数的。" % (
                _pp(overall["mean_pred"]), _pp(overall["base_rate"]), abs(gap) * 100)))

    # --- 最偏的那一档 ---
    buckets = [b for b in out["reliability"] if b["n"] >= 2]
    if buckets:
        worst = max(buckets, key=lambda b: abs(b["mean_pred"] - b["mean_outcome"]))
        wgap = worst["mean_pred"] - worst["mean_outcome"]
        if abs(wgap) >= 0.15:
            ins.append(_insight(
                "warn", "最偏的一档：你说 %s" % _pp(worst["mean_pred"]),
                "这一档有 %d 条，实际只发生了 %s（偏了 %.0f 个百分点）。"
                "去翻一翻这几条，看看当时是什么让你这么有把握。" % (
                    worst["n"], _pp(worst["mean_outcome"]), abs(wgap) * 100)))

    # --- 敢不敢下判断 ---
    timid = n >= 8 and overall["sharpness"] < 0.08 and 0.35 <= overall["mean_pred"] <= 0.65
    if timid:
        ins.append(_insight(
            "warn", "你几乎总在说 50%",
            "你的概率标准差只有 %.3f，平均值又停在 %s —— 意思是无论什么事，"
            "你都给个四五十。这不是谨慎，是没判断。"
            "试着把真正有把握的事说到 80%%。" % (
                overall["sharpness"], _pp(overall["mean_pred"]))))

    # --- 区分度 ---
    auc = overall["auc"]
    if auc is not None and n >= 10:
        if auc < 0.60:
            ins.append(_insight(
                "warn", "你的概率没能把两种结果分开",
                "区分度 AUC = %.2f（0.5 等于抛硬币）。也就是说，"
                "你会发生的那批事，和你不会发生的那批事，给出的概率差不多。"
                "问题不在校准，在于判断依据本身还不够分得开。" % auc))
        elif auc >= 0.75:
            ins.append(_insight(
                "good", "排序能力不错",
                "区分度 AUC = %.2f，说明你能把「更可能发生」和「更不可能发生」排对。"
                "这时候再修一修整体偏高偏低，准度就上来了。" % auc))

    # --- Murphy 分解：该练校准还是练依据 ---
    mp = overall.get("murphy")
    if mp and n >= 10:
        if mp["reliability"] >= 0.02:
            ins.append(_insight(
                "warn", "先练校准，再练判断",
                "Brier 分解里「可靠性」= %.3f（越大越说明你说的概率和真实频率对不上）。"
                "校准是最好练的一环：什么都不用改，只把你写下的数字往实际方向挪一挪。" % mp["reliability"]))
        elif mp["resolution"] <= 0.02:
            ins.append(_insight(
                "info", "校准没问题，依据还太粗",
                "「可靠性」已经很好了（%.3f），但「分辨力」只有 %.3f —— "
                "你报的数字很诚实，只是还没抓住真正能区分结果的线索。" % (
                    mp["reliability"], mp["resolution"])))

    # --- 证据有没有用 ---
    if n >= 3 and ev["n_bets"] == 0:
        ins.append(_insight(
            "info", "你还没记过任何证据",
            "这时候这个工具只是预测记录本，没在练贝叶斯。"
            "下次写完之后，随手记一条你观察到的线索，看看它该把概率推向哪边。"))
    elif ev["n_bets"] > 0:
        if ev["mean_abs_move"] < 0.03 and ev["n_evidence"] >= 5:
            ins.append(_insight(
                "warn", "你记的证据基本没推动概率",
                "%d 条证据平均只把概率推动 %.0f 个百分点，多数 LR 接近 1。"
                "LR≈1 的意思是：这件事发生也好、不发生也好，你都会看到它 —— 那它就不是证据。"
                "下次记之前先问一句：如果不发生，我还会看到它吗？" % (
                    ev["n_evidence"], ev["mean_abs_move"] * 100)))
        if ev["hurt"] > ev["helped"] and (ev["hurt"] + ev["helped"]) >= 4:
            ins.append(_insight(
                "warn", "你的证据整体帮了倒忙",
                "有 %d 次，加了证据之后反而离结果更远；帮上忙的只有 %d 次。"
                "通常是因为把「听起来相关」的东西当成了证据。" % (ev["hurt"], ev["helped"])))
        elif ev["helped"] >= 4 and ev["helped"] > ev["hurt"] * 2:
            ins.append(_insight(
                "good", "你是会搜集证据的",
                "加了证据之后更接近结果的有 %d 次，被带偏的只有 %d 次。"
                "这说明你挑的线索大多是真的有信息量的。" % (ev["helped"], ev["hurt"])))

    # --- 分领域 ---
    strong = [d for d in domains if d["n"] >= 3]
    if strong:
        worst_d = max(strong, key=lambda d: d["bias"])
        best_d = min(strong, key=lambda d: abs(d["bias"]))
        if worst_d["bias"] >= 0.15:
            ins.append(_insight(
                "warn", "「%s」是你的重灾区" % worst_d["domain"],
                "这一类有 %d 条，平均报 %s，实际只发生 %s —— 高估 %.0f 个百分点。"
                "比整体水平明显更差，值得单独想一想为什么。" % (
                    worst_d["n"], _pp(worst_d["mean_pred"]), _pp(worst_d["base_rate"]),
                    worst_d["bias"] * 100)))
        if best_d["domain"] != worst_d["domain"] and best_d["n"] >= 3 and abs(best_d["bias"]) <= 0.08:
            ins.append(_insight(
                "good", "「%s」上你判断得挺准" % best_d["domain"],
                "%d 条平均报 %s，实际 %s，几乎没偏。"
                "可以回想一下：这一类你是怎么想的？把同样的方式搬到别的领域去。" % (
                    best_d["n"], _pp(best_d["mean_pred"]), _pp(best_d["base_rate"]))))

    # --- 最近有没有变好 ---
    if drift:
        if drift["improving"]:
            ins.append(_insight(
                "good", "最近在收敛",
                "最近的 %d 条偏差是 %.0f 个百分点，比之前的 %.0f 个百分点小。"
                "保持住，这种变化比单条预测的对错重要得多。" % (
                    drift["late_n"], abs(drift["late_bias"]) * 100,
                    abs(drift["early_bias"]) * 100)))
        elif drift["worsening"]:
            ins.append(_insight(
                "warn", "最近偏差在变大",
                "最近的 %d 条偏差升到 %.0f 个百分点（之前是 %.0f）。"
                "想一想最近是不是写得更随意了，或者遇上了一个特别难的领域。" % (
                    drift["late_n"], abs(drift["late_bias"]) * 100,
                    abs(drift["early_bias"]) * 100)))

    # --- 自信地错 ---
    conf_wrong = [r for r in settled
                  if abs(r["posterior"] - 0.5) >= 0.35
                  and (r["posterior"] > 0.5) != (r["outcome"] == 1)]
    if len(conf_wrong) >= 3:
        ins.append(_insight(
            "warn", "自信地错了 %d 次" % len(conf_wrong),
            "这些是你给出极端概率（≥85% 或 ≤15%）却猜反的。"
            "极端概率的代价特别高，写之前值得多停三秒。"))

    return out
