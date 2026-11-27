"""スマホ用の静的ページ（site/）を書き出す。

site/index.html                 … 画面（static/mobile.html をコピー）
site/data/meta.json             … 更新日時・対象日・リポジトリ名
site/data/day-YYYY-MM-DD.json   … その日のF1全レース。Sの選び方・種別の絞り込み・照合範囲ごとの分析結果
site/data/trend.json            … 傾向・競輪場の集計に使う全レースの要約（ページ側で絞り込んで集計）
site/data/riders.json           … 選手の一覧（最新のプロフィールと回数）
site/data/r/<選手キー>.json      … 選手ごとの過去レース（新しい順20件）
メモ（notes/）は notes/notes.json をページが直接読む（書き込みから数分で反映）
"""
from __future__ import annotations

import json
import os
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import analysis as an
import kd_scraper as kd
import nk_scraper as nk
import notes as nt

ROOT = Path(__file__).parent
JST = timezone(timedelta(hours=9))


def dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def ref_row(lk: dict) -> list:
    """過去レース1件をページ用に短くする"""
    c = lk["cars"]
    return [lk["text"], lk["cat"], lk["url"], lk["video"], lk["L"], c["m"], c["s"], c["b"], lk["combo"], lk["tri"]]


def _compact_scope(sc: dict, refs: dict) -> dict:
    def ref(lk):
        refs[lk["id"]] = ref_row(lk)
        return lk["id"]

    out = dict(name=sc["name"], n=sc["n"], level=sc.get("level"))
    if not sc["n"]:
        return out
    out.update(
        tri_med=sc["tri_med"], manken=sc["manken"], tsuppari=sc["tsuppari"], kimarite=sc["kimarite"],
        n_conv=sc["n_conv"],
        lines=[dict(win=p["win"], top3=p["top3"], lead=p["lead"]) for p in sc["per_line"]],
        exacta=[[x["combo"], x["n"], x["tri_med"]] for x in sc["exacta"][:6]],
        tri=[dict(c=t["combo"], n=t["n"], p=t["pattern"], m=t["tri_med"], x=t["tri_max"],
                  r=[ref(l) for l in t["races"][:4]]) for t in sc["trifecta"][:8]],
        manken_tri=[dict(c=t["combo"], n=t["n"], p=t["pattern"], m=t["tri_med"], x=t["tri_max"],
                         r=[ref(l) for l in t["races"][:3]]) for t in sc["tri_manken"][:5]],
        races=[ref(l) for l in sc["races"][:30]],
    )
    return out


def rider_view(x: "kd.Rider") -> dict:
    n = x.nk or {}
    return dict(car=x.car, name=x.name, mark=x.mark, score=x.score, pref=x.pref or n.get("pref", ""),
                term=x.term or n.get("term"), klass=x.klass,
                style=x.style, key=an.rider_key(dict(nk=n, name=x.name)),
                st=[n.get(k) for k in ("s", "h", "b", "nige", "makuri", "sashi", "mark")] if n else None,
                type=an.leader_type(n) if n else None)


def race_item(ds, refs, *, venue, no, cat, category, cls, url, lines, riders: list[dict], exclude_id=None) -> dict:
    """1レース分（出走表または手入力）をページ用にまとめ、Sの選び方・種別の絞り込みごとに照合する。
    riders: [{car, name, mark, score, pref, st, type, key, nk}]"""
    rmap = {}
    for x in riders:  # 手入力などで nk がないときは表示用の回数（st=[S,H,B,…]）から作る
        y = dict(x)
        if not y.get("nk") and y.get("st"):
            y["nk"] = dict(s=y["st"][0], h=y["st"][1], b=y["st"][2])
        rmap[x["car"]] = y
    leaders = {x["car"]: x.get("type") for x in riders if x.get("type")}
    item = dict(no=no, cat=cat, category=category, cls=cls, url=url, lines=lines, venue=venue,
                vpref=an.VENUE_PREF.get(venue, ""),
                type=an.line_type(lines) if lines else None,
                riders=[{k: v for k, v in x.items() if k != "nk"} for x in riders],
                tags=[an.line_features(l, rmap, venue) for l in lines])
    if exclude_id:  # 終わったレース自身は照合に使わない
        ds = [d for d in ds if d["id"] != exclude_id]
    if lines and cls != "ガールズ":
        marks = {x["car"]: x["mark"] for x in riders if x.get("mark")}
        res = {}
        for same in (True, False):
            for s in [None] + list(range(len(lines))):
                m = an.match(ds, lines, marks, s, category=category, cls=cls, venue=venue,
                             same_category=same, leaders=leaders)
                key = ("n" if s is None else str(s)) + ("" if same else "a")
                res[key] = dict(default=m["default"],
                                scopes={k: _compact_scope(v, refs) for k, v in m["scopes"].items()})
        item["res"] = res
    return item


def item_from_race(ds, refs, r: "kd.Race", venue: str) -> dict:
    riders = []
    for x in r.riders:
        v = rider_view(x)
        v["nk"] = x.nk
        riders.append(v)
    return race_item(ds, refs, venue=venue, no=r.race_no, cat=r.category_raw, category=r.category, cls=r.cls,
                     url=r.url.replace("?pageType=result", ""), lines=r.lines, riders=riders, exclude_id=r.race_id)


def build_day(ds: list[dict], d: date, log=print) -> dict:
    venues = []
    refs: dict = {}
    for md in kd.list_day(d, "racecard"):
        if md.grade != "F1":
            continue
        races = []
        for rid in md.race_ids:
            try:
                r = kd.fetch_race(rid, md.venue_slug, result=False)
                if r:
                    nk.enrich(r)
            except Exception as e:
                log(f"skip {rid}: {e}")
                continue
            if not r:
                continue
            item = item_from_race(ds, refs, r, md.venue)
            item["rid"], item["slug"] = rid, md.venue_slug
            races.append(item)
        if races:
            venues.append(dict(venue=md.venue, bank=an.bank_of(md.venue), races=races))
    return dict(date=d.isoformat(), venues=venues, refs=refs)


def trend_rows(ds: list[dict]) -> dict:
    """傾向・競輪場タブ用：1レース1行の要約（列名は cols）"""
    cols = ["date", "venue", "bank", "cls", "cat", "type", "shape", "n", "s", "s_win", "s_top3", "tsu",
            "man", "tri", "kim", "hon", "tai", "low", "s_lead"]
    rows = []
    for d in ds:
        sl = d["L"][d["s_line"]] if d["s_line"] is not None else None

        def ml(l):
            return [int(l["win"]), int(l["top2"]), int(l["top3"]), int(l["is_s"])] if l else None
        low = d["lowest"]
        rows.append([d["date"], d["venue"], d["bank"], d["cls"], d["category"], d["type"], d["shape"], d["n_cars"],
                     an.line_label(sl["size"], sl["marks"]) if sl else None,
                     int(sl["win"]) if sl else None, int(sl["top3"]) if sl else None, int(d["tsuppari"]),
                     int(d["manken"]), d["tri_yen"], d["kimarite"] or None, ml(d["hon_line"]), ml(d["tai_line"]),
                     [low["pos"] if low["pos"] == "単騎" else
                      (f"{low['line_mark']}ライン" if low["line_mark"] != "無" else "印なしライン") + "の" + low["pos"],
                      low["rank"]] if low and low["rank"] is not None else None,
                     d["s_leader"]])
    # ラインの特徴別の成績用：2車以上のライン1本1行
    lcols = ["race", "size", "s", "win", "top3", "l12", "bwin", "same", "local", "jiriki"]
    b = lambda v: None if v is None else int(v)
    lrows = [[i, l["size"], int(l["is_s"]), int(l["win"]), int(l["top3"]), int(l["line12"]), int(l["bante_win"]),
              b(l["same"]), b(l["local"]), b(l["jiriki"])]
             for i, d in enumerate(ds) for l in d["L"] if l["size"] >= 2]
    return dict(cols=cols, rows=rows, lcols=lcols, lrows=lrows, banks=an.BANKS, types=an.LINE_TYPES, leaders=an.LEADER_TYPES,
                categories=kd.CATEGORY_ORDER,
                venues=sorted({d["venue"] for d in ds}, key=lambda v: (an.BANKS.index(an.bank_of(v)), v)),
                bank333=sorted(an.BANK_333), bank500=sorted(an.BANK_500))


def build_riders(ds: list[dict], races_raw: dict, out: Path):
    """選手一覧と、選手ごとの過去レース（新しい順20件）"""
    hist = defaultdict(list)
    for d in ds:  # ds は新しい順
        for key, car in d["rider_keys"]:
            if len(hist[key]) < 20:
                hist[key].append((d, car))
    prof = {}
    for d in ds:
        raw = races_raw.get(d["id"])
        if not raw:
            continue
        for r in raw["riders"]:
            key = an.rider_key(r)
            if key in prof:
                continue
            n = r.get("nk") or {}
            prof[key] = dict(key=key, name=r["name"], pref=r.get("pref", ""), term=r.get("term"), klass=r.get("klass", ""),
                             score=r.get("score"), style=r.get("style", ""),
                             st=[n.get(k) for k in ("s", "h", "b", "nige", "makuri", "sashi", "mark")] if n else None,
                             type=an.leader_type(n) if n else None, last=d["date"])
    rdir = out / "data" / "r"
    if rdir.exists():
        shutil.rmtree(rdir)
    for key, items in hist.items():
        rows = []
        for d, car in items:
            lk = an.link(d)
            rows.append(dict(car=car, rank=d["cars"]["rank"].get(car) or d["cars"]["rank"].get(str(car), ""),
                             race=ref_row(lk)))
        dump(rdir / f"{key}.json", dict(profile=prof.get(key), races=rows))
    riders = sorted(prof.values(), key=lambda p: p["name"])
    dump(out / "data" / "riders.json", [[p["key"], p["name"], p["pref"], p["term"], p["klass"], p["score"], p["type"], p["last"]]
                                        for p in riders])


def build_site(ds: list[dict], days: list[date], out: Path = ROOT / "site", log=print, races_raw=None):
    (out / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "static" / "mobile.html", out / "index.html")
    names = []
    for d in days:
        log(f"{d} の出走表を分析中…")
        dump(out / "data" / f"day-{d.isoformat()}.json", build_day(ds, d, log))
        names.append(d.isoformat())
    log("傾向・選手を集計中…")
    dump(out / "data" / "trend.json", trend_rows(ds))
    if races_raw is not None:
        build_riders(ds, races_raw, out)
    nt.compile_notes()
    shutil.copy(nt.NOTES_JSON, out / "data" / "notes.json")
    meta = dict(updated=datetime.now(JST).strftime("%m/%d %H:%M"), days=names, count=len(ds),
                oldest=min((x["date"] for x in ds), default=None), last=max((x["date"] for x in ds), default=None),
                repo=os.environ.get("GITHUB_REPOSITORY", "ryuuumi/keirin-f1"))
    dump(out / "data" / "meta.json", meta)
    for f in (out / "data").glob("day-*.json"):
        if f.stem[4:] not in names:
            f.unlink()
