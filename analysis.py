"""保存済みレースから特徴量を作り、集計・当日レースとの照合を行う。

分析ロジックはここだけにまとめてある。保存しているのは取得した生データなので、
ここを書き換えればアプリを再起動するだけで全レースに反映される（再取得は不要）。
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict

MARK_ORDER = ["◎", "○", "▲", "△", "×", "注"]
MANKEN = 10_000
POS_NAME = ["先頭", "番手", "3番手", "4番手", "5番手"]

# 周長（Kドリームス出走表の「同走路年間勝利度数【xxx】」で確認。前橋は335mだが333m扱い）
BANK_333 = {"前橋", "松戸", "小田原", "伊東", "富山", "奈良", "防府"}
BANK_500 = {"宇都宮", "大宮", "高知"}
BANKS = ["333m", "400m", "500m"]


def bank_of(venue: str, raw=None) -> str:
    if raw:
        n = int(raw)
        return "333m" if n < 380 else ("500m" if n >= 450 else "400m")
    if venue in BANK_333:
        return "333m"
    if venue in BANK_500:
        return "500m"
    return "400m"


# ------------------------------------------------------------ ラインの区分
def line_shape(lines: list[list[int]]) -> str:
    return "-".join(str(n) for n in sorted((len(l) for l in lines), reverse=True))


def line_type(lines: list[list[int]]) -> str:
    sizes = [len(l) for l in lines]
    multi = sum(1 for s in sizes if s >= 2)
    single = sum(1 for s in sizes if s == 1)
    if not sizes:
        return "不明"
    if multi == 3 and single <= 1:
        return "三分戦"
    if multi == 2 and single <= 1:
        shape = line_shape(lines)
        if shape in ("4-3", "3-3-1"):
            return f"二分戦({shape})"
        return "二分戦"
    if multi == 2:
        return "二分戦コマ切れ"
    if multi == 1:
        return "その他"
    if multi >= 4 or (multi == 3 and single >= 2):
        return "超コマ切れ"
    return "超コマ切れ"


# 競輪場の所在県（「地元」の判定に使う）
VENUE_PREF = {
    "函館": "北海道", "青森": "青森", "いわき平": "福島", "弥彦": "新潟", "前橋": "群馬", "取手": "茨城",
    "宇都宮": "栃木", "大宮": "埼玉", "西武園": "埼玉", "京王閣": "東京", "立川": "東京", "松戸": "千葉",
    "千葉": "千葉", "川崎": "神奈川", "平塚": "神奈川", "小田原": "神奈川", "伊東": "静岡", "静岡": "静岡",
    "名古屋": "愛知", "豊橋": "愛知", "岐阜": "岐阜", "大垣": "岐阜", "富山": "富山", "松阪": "三重",
    "四日市": "三重", "福井": "福井", "奈良": "奈良", "向日町": "京都", "和歌山": "和歌山", "岸和田": "大阪",
    "玉野": "岡山", "広島": "広島", "防府": "山口", "高松": "香川", "小松島": "徳島", "高知": "高知",
    "松山": "愛媛", "小倉": "福岡", "久留米": "福岡", "武雄": "佐賀", "佐世保": "長崎", "別府": "大分",
    "熊本": "熊本",
}


def rider_pref(r: dict) -> str:
    return r.get("pref") or (r.get("nk") or {}).get("pref") or ""


def is_jiriki(nk: dict | None) -> bool | None:
    """自力選手か：直近4か月で H か B を2回以上"""
    if not nk:
        return None
    return nk.get("h", 0) + nk.get("b", 0) >= 2


def line_features(cars: list[int], riders: dict, venue: str) -> dict:
    """ラインの特徴：同県（同じ府県が2人以上）・地元（開催場の県の選手がいる）・番手が自力"""
    prefs = [rider_pref(riders[c]) for c in cars if c in riders]
    known = [p for p in prefs if p]
    vp = VENUE_PREF.get(venue)
    return dict(
        same=None if len(known) < 2 else len(known) != len(set(known)),
        local=None if not known else (vp in known),
        jiriki=is_jiriki(riders[cars[1]].get("nk")) if len(cars) >= 2 and cars[1] in riders else None,
    )


LINE_TYPES = ["三分戦", "二分戦(4-3)", "二分戦(3-3-1)", "二分戦", "二分戦コマ切れ", "その他", "超コマ切れ"]
LEADER_TYPES = ["先行型", "先行型(叩かれ)", "捲り型", "自在型", "追込型"]


def leader_type(nk: dict | None) -> str | None:
    """netkeirin の直近4か月の回数から選手のタイプを決める（境目はここで調整）"""
    if not nk:
        return None
    n = max(1, nk.get("r1", 0) + nk.get("r2", 0) + nk.get("r3", 0) + nk.get("rx", 0))
    h, b = nk.get("h", 0) / n, nk.get("b", 0) / n
    nige, makuri = nk.get("nige", 0), nk.get("makuri", 0)
    if b >= 0.30:
        return "先行型"
    if h >= 0.25:
        return "先行型(叩かれ)"
    if makuri > nige and (nige + makuri) / n >= 0.12:
        return "捲り型"
    if h + b >= 0.08 or (nige + makuri) / n >= 0.08:
        return "自在型"
    return "追込型"


def mark_tag(marks: list[str]) -> str:
    for m in ("◎", "○", "▲"):
        if m in marks:
            return m
    return "無"


def size_bucket(n: int) -> str:
    return "単騎" if n == 1 else f"{min(n, 4)}車"


def line_key(size: int, marks: list[str], is_s: bool) -> str:
    """過去レースのラインと当日のラインを対応させるためのキー。例: '3車◎', '2車○S', '単騎無'"""
    return f"{size_bucket(size)}{mark_tag(marks)}{'S' if is_s else ''}"


def line_label(size: int, marks: list[str], is_s: bool = False) -> str:
    t = mark_tag(marks)
    base = f"{t}ライン" if t in ("◎", "○", "▲") else ("単騎" if size == 1 else f"{size}車ライン")
    if t in ("◎", "○", "▲") and size == 1:
        base = f"{t}単騎"
    return base + ("(S)" if is_s else "")


# ------------------------------------------------------------ 1レース分の特徴量
def derive(race: dict) -> dict | None:
    riders = {r["car"]: r for r in race["riders"] if "欠" not in (r.get("rank_text") or "")}
    lines = [[c for c in l if c in riders] for l in race.get("lines") or []]
    lines = [l for l in lines if l]
    if not riders or not lines:
        return None
    car_line = {c: i for i, l in enumerate(lines) for c in l}
    for c in riders:  # 並びにいない選手は単騎扱い
        if c not in car_line:
            lines.append([c])
            car_line[c] = len(lines) - 1

    scores = [r["score"] for r in riders.values() if r.get("score")]
    low_score = min(scores) if scores else None
    s_line = next((car_line[c] for c, r in riders.items() if "S" in (r.get("sb") or "")), None)
    b_line = next((car_line[c] for c, r in riders.items() if "B" in (r.get("sb") or "")), None)

    order = sorted((r for r in riders.values() if r.get("rank")), key=lambda r: r["rank"])
    top = [r["car"] for r in order[:3]]

    L = []
    for i, cars in enumerate(lines):
        marks = [riders[c].get("mark", "") for c in cars]
        ranks = [riders[c].get("rank") for c in cars]
        is_s = i == s_line
        L.append(dict(
            idx=i, cars=cars, size=len(cars), marks=marks,
            key=line_key(len(cars), marks, is_s),
            key_nos=line_key(len(cars), marks, False),
            key_size=f"{size_bucket(len(cars))}{'S' if is_s else ''}",
            leader=leader_type(riders[cars[0]].get("nk")) if len(cars) >= 1 else None,
            **line_features(cars, riders, race["venue"]),
            bante_win=len(cars) >= 2 and bool(top) and top[0] == cars[1],
            line12=len(top) >= 2 and top[0] in cars and top[1] in cars,
            label=line_label(len(cars), marks, is_s),
            is_s=is_s, is_b=i == b_line,
            win=bool(top) and top[0] in cars,
            top2=any(c in cars for c in top[:2]),
            top3=any(c in cars for c in top),
            n_top3=sum(1 for c in top if c in cars),
            best_rank=min([x for x in ranks if x] or [99]),
            avg_score=statistics.mean([riders[c]["score"] for c in cars if riders[c].get("score")] or [0]),
        ))

    tri = race.get("payouts", {}).get("3連単") or {}
    exa = race.get("payouts", {}).get("2車単") or {}
    w = top[0] if top else None
    wline = car_line.get(w)
    sec = top[1] if len(top) > 1 else None
    thi = top[2] if len(top) > 2 else None

    def pos(c):
        return lines[car_line[c]].index(c) if c in car_line else None

    kimarite = riders[w].get("kimarite", "") if w else ""
    # 展開パターン（主導権 → 1着ライン → 2着の関係）
    if b_line is None:
        lead = "主導権不明"
    elif b_line == s_line:
        lead = "Sラインが主導権"
    else:
        lead = f"{L[b_line]['label']}が主導権"
    if w is None:
        pattern = lead
    else:
        same2 = sec is not None and car_line.get(sec) == wline
        pattern = (f"{lead} → 1着 {L[wline]['label']}"
                   f"{POS_NAME[min(pos(w), 4)]}{('(' + kimarite + ')') if kimarite else ''}"
                   f" / 2着 {'同ライン' if same2 else '別線'}")

    six = riders.get(6)
    lowest = [c for c, r in riders.items() if r.get("score") == low_score] if low_score else []

    def rider_info(c):
        if c is None or c not in riders:
            return None
        li = L[car_line[c]]
        p = pos(c)
        return dict(car=c, rank=riders[c].get("rank"),
                    pos="単騎" if li["size"] == 1 else POS_NAME[min(p, 4)],
                    line_label=li["label"], line_mark=mark_tag(li["marks"]))

    return dict(
        id=race["race_id"], url=race["url"], venue=race["venue"], date=race["date"],
        bank=bank_of(race["venue"], race.get("bank")),
        race_no=race["race_no"], category=race["category"], cls=race["cls"], grade=race.get("grade"),
        title=race.get("title", ""), n_cars=len(riders),
        lines=lines, shape=line_shape(lines), type=line_type(lines),
        line_str=" ".join("".join(map(str, l)) for l in lines),
        L=L, s_line=s_line, b_line=b_line, win_line=wline,
        s_key=L[s_line]["key"] if s_line is not None else None,
        s_size=size_bucket(L[s_line]["size"]) if s_line is not None else None,
        tsuppari=s_line is not None and s_line == b_line,
        top=top, kimarite=kimarite, pattern=pattern,
        win=(L[wline]["key"], pos(w), L[wline]["key_size"]) if w else None,
        second=(L[car_line[sec]]["key"], pos(sec), car_line[sec] == wline, L[car_line[sec]]["key_size"]) if sec else None,
        third=(L[car_line[thi]]["key"], pos(thi)) if thi else None,
        top_lp=[(car_line[c], pos(c)) for c in top],  # 1〜3着の (ライン番号, ライン内の位置)
        tri_yen=tri.get("yen"), tri_pop=tri.get("pop"), tri_combo=tri.get("combo"),
        exa_yen=exa.get("yen"),
        manken=bool(tri.get("yen") and tri["yen"] >= MANKEN),
        six=rider_info(6) if six else None,
        lowest=rider_info(lowest[0]) if len(lowest) == 1 else None,
        mark_sig=tuple(sorted(l["key_nos"] for l in L)),
        lead_sig=tuple(sorted(f"{l['key_nos']}{l['leader']}" for l in L)),
        s_leader=L[s_line]["leader"] if s_line is not None else None,
        cars=dict(m={c: r.get("mark", "") for c, r in riders.items() if r.get("mark")},
                  s=next((c for c, r in riders.items() if "S" in (r.get("sb") or "")), None),
                  b=next((c for c, r in riders.items() if "B" in (r.get("sb") or "")), None),
                  rank={c: r.get("rank_text") or "" for c, r in riders.items()}),
        rider_keys=[(rider_key(r), c) for c, r in riders.items()],
        venue_code=race["race_id"][:2], day_no=race.get("day_no"), run_date=race["date"],
        hon_line=next((l for l in L if "◎" in l["marks"]), None),
        tai_line=next((l for l in L if "○" in l["marks"]), None),
    )


def rider_key(r: dict) -> str:
    nk = r.get("nk") or {}
    return f"nk{nk['nk_id']}" if nk.get("nk_id") else f"nm{r.get('name', '').replace(' ', '')}"


def digest_url(d: dict) -> str:
    return (f"https://keirin.kdreams.jp/stadium/digest/?kyogijoCd={d['venue_code']}"
            f"&kaisaiDate={d['run_date'].replace('-', '')}&gradeCd=2&raceNo={d['race_no']}")


def derive_all(races: list[dict]) -> list[dict]:
    out = []
    for r in races:
        try:
            d = derive(r)
        except Exception:
            d = None
        if d:
            out.append(d)
    out.sort(key=lambda d: (d["date"], d["venue"], d["race_no"]), reverse=True)
    return out


# ------------------------------------------------------------ 集計の道具
def pct(n, d):
    return round(100 * n / d, 1) if d else None


def med(xs):
    xs = [x for x in xs if x]
    return int(statistics.median(xs)) if xs else None


def avg(xs):
    xs = [x for x in xs if x]
    return int(statistics.mean(xs)) if xs else None


def link(d):
    return dict(id=d["id"], cat=f"{d['cls']}{d['category']}", url=d["url"], text=f"{d['venue']} {d['date'][2:].replace('-', '/')} {d['race_no']}R",
                video=digest_url(d), L=d["lines"], cars=d["cars"],
                lines=d["line_str"], tri=d["tri_yen"], combo=d["tri_combo"])


def apply_filters(ds, f: dict):
    def ok(d):
        if f.get("cls") and f["cls"] != "すべて":
            if f["cls"] == "男子" and d["cls"] == "ガールズ":
                return False
            if f["cls"] not in ("男子",) and d["cls"] != f["cls"]:
                return False
        for k in ("category", "type", "shape", "bank", "venue"):
            v = f.get(k)
            if v and v not in ("すべて", "全場") and d[k] != v:
                return False
        if f.get("n_cars") and str(d["n_cars"]) != str(f["n_cars"]):
            return False
        if f.get("since") and d["date"] < f["since"]:
            return False
        return True
    return [d for d in ds if ok(d)]


def summary_row(ds):
    return dict(n=len(ds), manken=pct(sum(d["manken"] for d in ds), len(ds)),
                tri_med=med(d["tri_yen"] for d in ds), tri_avg=avg(d["tri_yen"] for d in ds))


# ------------------------------------------------------------ 傾向分析タブ
def stats(ds):
    res = {"summary": summary_row(ds)}

    # S ラインの種類別
    g = defaultdict(list)
    for d in ds:
        if d["s_line"] is None:
            continue
        sl = d["L"][d["s_line"]]
        g[(d["type"], line_label(sl["size"], sl["marks"]))].append((d, sl))
    rows = []
    for (t, lab), items in g.items():
        n = len(items)
        rows.append(dict(type=t, s=lab, n=n,
                         win=pct(sum(sl["win"] for _, sl in items), n),
                         top3=pct(sum(sl["top3"] for _, sl in items), n),
                         tsuppari=pct(sum(d["tsuppari"] for d, _ in items), n),
                         manken=pct(sum(d["manken"] for d, _ in items), n),
                         tri_med=med(d["tri_yen"] for d, _ in items)))
    res["s_table"] = sorted([r for r in rows if r["n"] >= 5], key=lambda r: (r["type"], -r["n"]))

    # ◎・○ライン
    def mark_rows(key):
        items = [(d, d[key]) for d in ds if d[key]]
        n = len(items)
        if not n:
            return None
        dead = [d for d, l in items if not l["top3"]]
        return dict(n=n, win=pct(sum(l["win"] for _, l in items), n),
                    top2=pct(sum(l["top2"] for _, l in items), n),
                    top3=pct(sum(l["top3"] for _, l in items), n),
                    dead=pct(len(dead), n), dead_tri=avg(d["tri_yen"] for d in dead),
                    as_s=pct(sum(l["is_s"] for _, l in items), n))
    res["marks"] = {"◎ライン": mark_rows("hon_line"), "○ライン": mark_rows("tai_line")}

    # ◎ラインが S を取った／取らなかった
    hs = defaultdict(list)
    for d in ds:
        if d["hon_line"] and d["s_line"] is not None:
            hs["◎ラインがS" if d["hon_line"]["is_s"] else "◎ライン以外がS"].append(d)
    res["hon_by_s"] = [dict(case=k, n=len(v), win=pct(sum(x["hon_line"]["win"] for x in v), len(v)),
                            top3=pct(sum(x["hon_line"]["top3"] for x in v), len(v)),
                            manken=pct(sum(x["manken"] for x in v), len(v)),
                            tri_med=med(x["tri_yen"] for x in v)) for k, v in sorted(hs.items())]

    # ⑥番車・最低得点者
    def rider_rows(key):
        g = defaultdict(list)
        for d in ds:
            x = d[key]
            if x and x["rank"] is not None:
                g[f"{x['line_mark'] + 'ライン' if x['line_mark'] != '無' else '印なしライン'}の{x['pos']}"
                  if x["pos"] != "単騎" else "単騎"].append((d, x))
        rows = []
        for k, v in g.items():
            hit = [d for d, x in v if x["rank"] <= 3]
            rows.append(dict(case=k, n=len(v), top3=pct(len(hit), len(v)),
                             win=pct(sum(1 for _, x in v if x["rank"] == 1), len(v)),
                             tri_hit=avg(d["tri_yen"] for d in hit)))
        return sorted([r for r in rows if r["n"] >= 5], key=lambda r: -r["n"])
    res["six"] = rider_rows("six")
    res["lowest"] = rider_rows("lowest")

    # 決まり手
    km = defaultdict(Counter)
    for d in ds:
        if d["kimarite"]:
            km[d["type"]][d["kimarite"]] += 1
    res["kimarite"] = [dict(type=t, n=sum(c.values()), **{k: pct(c[k], sum(c.values())) for k in "逃捲差マ"})
                       for t, c in sorted(km.items(), key=lambda kv: -sum(kv[1].values()))]

    # 番組 × ライン区分
    gc = defaultdict(list)
    for d in ds:
        gc[(d["category"], d["type"])].append(d)
    from kd_scraper import CATEGORY_ORDER
    res["by_category"] = [dict(category=c, type=t, **summary_row(v),
                               tsuppari=pct(sum(x["tsuppari"] for x in v), len(v)))
                          for (c, t), v in sorted(gc.items(), key=lambda kv: (CATEGORY_ORDER.index(kv[0][0]) if kv[0][0] in CATEGORY_ORDER else 99, -len(kv[1])))
                          if len(v) >= 5]

    # 展開パターン（全体）
    res["patterns"] = pattern_table(ds, limit=15)
    return res


def pattern_table(ds, limit=10, min_n=2):
    g = defaultdict(list)
    for d in ds:
        g[d["pattern"]].append(d)
    rows = []
    for p, v in g.items():
        if len(v) < min_n:
            continue
        rows.append(dict(pattern=p, n=len(v), share=pct(len(v), len(ds)),
                         tri_med=med(d["tri_yen"] for d in v), tri_avg=avg(d["tri_yen"] for d in v),
                         manken=pct(sum(d["manken"] for d in v), len(v)),
                         races=[link(d) for d in v[:8]]))
    rows.sort(key=lambda r: -r["n"])
    return rows[:limit]


# ------------------------------------------------------------ 競輪場比較
def venues(ds):
    def row(v):
        n = len(v)
        withs = [d for d in v if d["s_line"] is not None]
        return dict(n=n,
                    s_top3=pct(sum(d["L"][d["s_line"]]["top3"] for d in withs), len(withs)),
                    s_win=pct(sum(d["L"][d["s_line"]]["win"] for d in withs), len(withs)),
                    tsuppari=pct(sum(d["tsuppari"] for d in withs), len(withs)),
                    nige=pct(sum(d["kimarite"] == "逃" for d in v), n),
                    makuri=pct(sum(d["kimarite"] == "捲" for d in v), n),
                    sashi=pct(sum(d["kimarite"] == "差" for d in v), n),
                    hon_win=pct(sum(bool(d["hon_line"] and d["hon_line"]["win"]) for d in v), n),
                    manken=pct(sum(d["manken"] for d in v), n),
                    tri_med=med(d["tri_yen"] for d in v))
    g = defaultdict(list)
    for d in ds:
        g[d["venue"]].append(d)
    groups = []
    for bk in BANKS:
        bd = [d for d in ds if d["bank"] == bk]
        if not bd:
            continue
        rows = [dict(venue=k, **row(v)) for k, v in g.items() if bank_of(k) == bk]
        rows.sort(key=lambda r: -r["n"])
        groups.append(dict(bank=bk, total=row(bd), rows=rows))
    return dict(total=row(ds), groups=groups)


# ------------------------------------------------------------ 当日レースとの照合
def describe_today(lines, marks: dict, s_line: int | None, leaders=None):
    L = []
    for i, cars in enumerate(lines):
        ms = [marks.get(c, "") for c in cars]
        L.append(dict(idx=i, cars=cars, size=len(cars), marks=ms,
                      key_nos=line_key(len(cars), ms, False),
                      leader=(leaders or {}).get(cars[0]),
                      key=line_key(len(cars), ms, i == s_line),
                      key_size=f"{size_bucket(len(cars))}{'S' if i == s_line else ''}",
                      label=line_label(len(cars), ms, i == s_line), is_s=i == s_line))
    return L


def match(ds, lines, marks, s_line, category=None, cls=None, venue=None, min_n=20,
          same_category=False, leaders=None):
    """当日の並び＋Sを取るラインを指定して、同じ型の過去レースを探す。
    「同じ周長」「この場」「全場」の3つの範囲で、それぞれ件数が少なければ条件を段階的にゆるめる。"""
    lines = [l for l in lines if l]
    TL = describe_today(lines, marks, s_line, leaders)
    shape, typ = line_shape(lines), line_type(lines)
    n_cars = sum(len(l) for l in lines)
    s_key = TL[s_line]["key"] if s_line is not None else None
    s_size = size_bucket(TL[s_line]["size"]) if s_line is not None else None
    base = [d for d in ds if d["n_cars"] == n_cars]
    if cls:  # S級はS級だけ、A級はA級だけ（チャレンジ・ガールズもそれぞれだけ）で照合
        base = [d for d in base if d["cls"] == cls]
    if same_category and category:
        base = [d for d in base if d["category"] == category]
    t_lead = tuple(sorted(f"{l['key_nos']}{l['leader']}" for l in TL))
    has_lead = any(l["leader"] for l in TL)

    t_sig = tuple(sorted(line_key(l["size"], l["marks"], False) for l in TL))

    levels = ([("並び・印・先頭タイプが一致", lambda d: d["shape"] == shape and d["lead_sig"] == t_lead)] if has_lead else []) + [
        ("並び・印が一致", lambda d: d["shape"] == shape and d["mark_sig"] == t_sig),
        ("並びの形が一致", lambda d: d["shape"] == shape),
        ("ライン区分が一致", lambda d: d["type"] == typ),
        ("ライン区分が一致（Sは車数のみ）", lambda d: d["type"] == typ),
    ]

    def search(pool, need):
        tried, chosen, label = [], [], ""
        for lab, cond in levels:
            cand = [d for d in pool if cond(d)]
            if s_key:
                if lab.endswith("（Sは車数のみ）"):
                    cand = [d for d in cand if d["s_size"] == s_size]
                else:
                    cand = [d for d in cand if d["s_key"] == s_key]
            tried.append(dict(level=lab, n=len(cand)))
            chosen, label = cand, lab
            if len(cand) >= need:
                break
        return dict(level=label, tried=tried, **_match_result(chosen, TL))

    bank = bank_of(venue) if venue else None
    scopes = {}
    if bank:
        scopes["bank"] = dict(name=bank, **search([d for d in base if d["bank"] == bank], min_n))
    if venue:
        scopes["venue"] = dict(name=venue, **search([d for d in base if d["venue"] == venue], min(min_n, 10)))
    scopes["all"] = dict(name="全場", **search(base, min_n))
    return dict(today=dict(lines=TL, shape=shape, type=typ, s_key=s_key, n_cars=n_cars, bank=bank, cls=cls,
                           category=category if same_category else None),
                scopes=scopes, default="bank" if bank else "all")


def _match_result(ds, TL):
    n = len(ds)
    if not n:
        return dict(n=0)
    # 当日の各ラインに対応する過去ラインの成績
    per_line = []
    for tl in TL:
        keyname = "key"
        hits = [(d, l) for d in ds for l in d["L"] if l["key"] == tl["key"]]
        if len(hits) < 10:  # 印まで一致するラインが少なければ車数とSだけで見る
            keyname = "key_size"
            hits = [(d, l) for d in ds for l in d["L"] if l["key_size"] == tl["key_size"]]
        k = len(hits)
        per_line.append(dict(idx=tl["idx"], cars=tl["cars"], label=tl["label"], n=k,
                             by="印・車数" if keyname == "key" else "車数のみ",
                             win=pct(sum(l["win"] for _, l in hits), k),
                             top2=pct(sum(l["top2"] for _, l in hits), k),
                             top3=pct(sum(l["top3"] for _, l in hits), k),
                             lead=pct(sum(l["is_b"] for _, l in hits), k),
                             win_tri=med(d["tri_yen"] for d, l in hits if l["win"])))

    # 過去レースの1〜3着を「どのラインの何番手か」で見て、当日の車番に置き換える
    def convert(d):
        used, mapping, cars = set(), {}, []
        for li, p in d.get("top_lp", []):
            if li not in mapping:
                hl = d["L"][li]
                tl = None
                for k in ("key", "key_size"):
                    cand = [t for t in TL if t[k] == hl[k] and t["idx"] not in used]
                    if len(cand) == 1:
                        tl = cand[0]
                        break
                if tl is None:
                    return None
                mapping[li] = tl
                used.add(tl["idx"])
            tl = mapping[li]
            if p >= tl["size"]:
                return None
            cars.append(tl["cars"][p])
        return cars

    conv = [(d, convert(d)) for d in ds]
    exa, tri = defaultdict(list), defaultdict(list)
    for d, c in conv:
        if c and len(c) >= 2:
            exa[tuple(c[:2])].append(d)
        if c and len(c) >= 3:
            tri[tuple(c[:3])].append(d)
    exacta = [dict(combo="-".join(map(str, k)), n=len(v), share=pct(len(v), n), tri_med=med(d["tri_yen"] for d in v))
              for k, v in sorted(exa.items(), key=lambda kv: -len(kv[1]))[:8]]

    def tri_rows(groups, limit):
        rows = []
        for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:limit]:
            pc_ = Counter(d["pattern"] for d in v)
            rows.append(dict(combo="-".join(map(str, k)), n=len(v), share=pct(len(v), n),
                             pattern=pc_.most_common(1)[0][0], patterns=len(pc_),
                             tri_med=med(d["tri_yen"] for d in v), tri_max=max([d["tri_yen"] or 0 for d in v]),
                             manken=pct(sum(d["manken"] for d in v), len(v)),
                             races=[link(d) for d in v[:6]]))
        return rows
    n_conv = sum(1 for _, c in conv if c and len(c) >= 3)
    trifecta = tri_rows(tri, 12)
    tri_manken = tri_rows({k: [d for d in v if d["manken"]] for k, v in tri.items() if any(d["manken"] for d in v)}, 8)

    manken = [d for d in ds if d["manken"]]
    s_rows = [d for d in ds if d["s_line"] is not None]
    return dict(
        n=n, manken=pct(len(manken), n), tri_med=med(d["tri_yen"] for d in ds),
        tri_avg=avg(d["tri_yen"] for d in ds),
        tsuppari=pct(sum(d["tsuppari"] for d in s_rows), len(s_rows)),
        kimarite={k: pct(sum(d["kimarite"] == k for d in ds), n) for k in "逃捲差マ"},
        per_line=per_line, exacta=exacta, trifecta=trifecta, tri_manken=tri_manken, n_conv=n_conv,
        patterns=pattern_table(ds, limit=8, min_n=1),
        manken_patterns=pattern_table(manken, limit=5, min_n=1),
        races=[link(d) for d in ds[:30]],
    )


def parse_lines_text(s: str) -> list[list[int]]:
    """'126-34-57' や '1 2 6 / 3 4 / 5 7' を [[1,2,6],[3,4],[5,7]] に"""
    import re
    s = s.replace("・", "-").replace("/", "-").replace("|", "-").replace("　", " ")
    out = []
    for part in re.split(r"[-ー－,、]+", s):
        cars = [int(c) for c in re.findall(r"[1-9]", part)]
        if cars:
            out.append(cars)
    return out
