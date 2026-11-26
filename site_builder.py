"""スマホ用の静的ページ（site/）を書き出す。

site/index.html           … 画面（static/mobile.html をコピー）
site/data/meta.json       … 更新日時・対象日
site/data/day-YYYY-MM-DD.json … その日のF1全レース。Sの選び方ごと・照合範囲ごとの分析結果
site/data/trend.json      … 傾向（周長×級ごとの集計）と競輪場比較
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import analysis as an
import kd_scraper as kd

ROOT = Path(__file__).parent
JST = timezone(timedelta(hours=9))


def _compact_scope(sc: dict, refs: dict) -> dict:
    """1つの照合範囲の結果から、ページで使う分だけ残す。過去レースは id で参照。"""
    def ref(lk):
        refs[lk["id"]] = [lk["text"], lk["cat"], lk["url"], lk["lines"], lk["combo"], lk["tri"]]
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
            except Exception as e:
                log(f"skip {rid}: {e}")
                continue
            if not r:
                continue
            item = dict(no=r.race_no, cat=r.category_raw, category=r.category, cls=r.cls,
                        url=r.url.replace("?pageType=result", ""), lines=r.lines,
                        riders=[dict(car=x.car, name=x.name, mark=x.mark, score=x.score) for x in r.riders])
            if r.lines and r.cls != "ガールズ":
                marks = {x.car: x.mark for x in r.riders if x.mark}
                res = {}
                for s in [None] + list(range(len(r.lines))):
                    m = an.match(ds, r.lines, marks, s, category=r.category, cls=r.cls, venue=r.venue)
                    res["n" if s is None else str(s)] = dict(
                        default=m["default"],
                        scopes={k: _compact_scope(v, refs) for k, v in m["scopes"].items()})
                item["res"] = res
            races.append(item)
        if races:
            venues.append(dict(venue=md.venue, bank=an.bank_of(md.venue), races=races))
    return dict(date=d.isoformat(), venues=venues, refs=refs)


def build_trend(ds: list[dict]) -> dict:
    out = {}
    for bank in ["すべて"] + an.BANKS:
        for cls in ["男子", "S級", "A級"]:
            f = {"cls": cls, "n_cars": "7"}
            if bank != "すべて":
                f["bank"] = bank
            st = an.stats(an.apply_filters(ds, f))
            st.pop("patterns", None)
            out[f"{bank}|{cls}"] = st
    venues = {cls: an.venues(an.apply_filters(ds, {"cls": cls, "n_cars": "7"})) for cls in ["男子", "S級", "A級"]}
    return dict(stats=out, venues=venues)


def build_site(ds: list[dict], days: list[date], out: Path = ROOT / "site", log=print):
    (out / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "static" / "mobile.html", out / "index.html")
    names = []
    for d in days:
        log(f"{d} の出走表を分析中…")
        day = build_day(ds, d, log)
        (out / "data" / f"day-{d.isoformat()}.json").write_text(
            json.dumps(day, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        names.append(d.isoformat())
    log("傾向を集計中…")
    (out / "data" / "trend.json").write_text(
        json.dumps(build_trend(ds), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    meta = dict(updated=datetime.now(JST).strftime("%m/%d %H:%M"), days=names, count=len(ds),
                oldest=min((x["date"] for x in ds), default=None), last=max((x["date"] for x in ds), default=None))
    (out / "data" / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    # 古い日のファイルを消す
    for f in (out / "data").glob("day-*.json"):
        if f.stem[4:] not in names:
            f.unlink()
