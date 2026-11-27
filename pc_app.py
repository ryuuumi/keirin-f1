"""PC版：スマホと同じ画面を自分のPCで動かす。  start.bat（Macは start.command）で起動。

- 取り込み済みの結果とメモは GitHub から（sync.py が起動時にダウンロード）
- 今日・明日の分析は GitHub Pages で作ったものを使い、なければこのPCで作る
- PCだけの機能：好きな日付の出走表、最新の並びでの作り直し、並びの手入力
"""
from __future__ import annotations

import json
import sys
import threading
import webbrowser
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory, abort

import analysis as an
import kd_scraper as kd
import nk_scraper as nk
import site_builder as sb
import store

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "pc"
DATA = OUT / "data"
PAGES = "https://ryuuumi.github.io/keirin-f1"
JST = timezone(timedelta(hours=9))
app = Flask(__name__)
STATE = {"ds": [], "ready": False}
_lock = threading.Lock()


def today() -> date:
    return datetime.now(JST).date()


def prepare():
    """取り込み済みの結果から、傾向・選手のデータを作る（起動時に1回）"""
    raw = []
    for f in sorted(store.RACES_DIR.glob("*/*/*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("has_result"):
                    raw.append(r)
    print(f"{len(raw)}レースを読み込み、集計しています…")
    ds = an.derive_all(raw)
    DATA.mkdir(parents=True, exist_ok=True)
    sb.dump(DATA / "trend.json", sb.trend_rows(ds))
    sb.build_riders(ds, {r["race_id"]: r for r in raw}, OUT)
    try:
        import notes as nt
        nt.compile_notes()
        (DATA / "notes.json").write_text(nt.NOTES_JSON.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass
    STATE["ds"] = ds
    STATE["meta"] = dict(count=len(ds), oldest=min((d["date"] for d in ds), default=None),
                         last=max((d["date"] for d in ds), default=None))
    STATE["ready"] = True
    print("準備ができました")


@app.get("/")
def index():
    return send_from_directory(ROOT / "static", "mobile.html")


@app.get("/data/meta.json")
def meta():
    t = today()
    m = dict(STATE.get("meta", {}), pc=True, ready=STATE["ready"],
             updated=datetime.now(JST).strftime("%m/%d %H:%M"),
             days=[t.isoformat(), (t + timedelta(days=1)).isoformat()], repo="ryuuumi/keirin-f1")
    return jsonify(m)


def day_file(d: str) -> Path:
    return DATA / f"day-{d}.json"


@app.get("/data/day-<d>.json")
def day(d):
    """今日・明日：GitHub Pages の分析を使う。なければ（または作り直し指定なら）このPCで作る"""
    f = day_file(d)
    if request.args.get("refresh") != "1":
        import time
        if f.exists() and time.time() - f.stat().st_mtime < 20 * 60:
            return send_from_directory(DATA, f.name)
        try:
            r = requests.get(f"{PAGES}/data/day-{d}.json", timeout=30)
            print(f"GitHubの分析 {d}: {r.status_code}")
            if r.ok and r.json().get("venues"):
                f.write_bytes(r.content)
                return send_from_directory(DATA, f.name)
        except Exception as e:
            print(f"GitHubの分析を取れませんでした：{e}")
    try:
        with _lock:
            print(f"{d} の出走表をこのPCで分析しています（1〜3分）…")
            out = sb.build_day(STATE["ds"], date.fromisoformat(d))
            sb.dump(f, out)
            print(f"{d} の分析ができました（{len(out['venues'])}場）")
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify(error=f"{type(e).__name__}: {e}"), 500
    return send_from_directory(DATA, f.name)


@app.get("/data/<path:p>")
def data_files(p):
    if not (DATA / p).exists():
        abort(404)
    return send_from_directory(DATA, p)


@app.get("/api/race")
def api_race():
    """最新の並びで1レースだけ作り直す"""
    rid, slug, venue = request.args["rid"], request.args["slug"], request.args["venue"]
    r = kd.fetch_race(rid, slug, result=False)
    if not r:
        return jsonify(error="出走表を読み取れませんでした"), 404
    nk.enrich(r)
    refs = {}
    item = sb.item_from_race(STATE["ds"], refs, r, venue)
    item["rid"], item["slug"] = rid, slug
    return jsonify(item=item, refs=refs)


@app.post("/api/manual")
def api_manual():
    """並びの手入力。b = {venue, lines:"42-175-63", marks:"4◎ 2○", category, cls, base:{riders...}}"""
    import re
    b = request.get_json()
    lines = an.parse_lines_text(b.get("lines", ""))
    if not lines:
        return jsonify(error="並びを入力してください（例：42-175-63）"), 400
    if len({c for l in lines for c in l}) != sum(len(l) for l in lines):
        return jsonify(error="同じ車番が2回入っています"), 400
    marks = {}
    for t in re.findall(r"[1-9]\s*[◎○▲△×注]|[◎○▲△×注]\s*[1-9]", b.get("marks", "")):
        marks[int(re.search(r"[1-9]", t).group())] = re.search(r"[◎○▲△×注]", t).group()
    base = {x["car"]: x for x in (b.get("riders") or [])}
    riders = []
    for c in sorted(c for l in lines for c in l):
        x = dict(base.get(c, {"car": c}))
        x["mark"] = marks.get(c, "") if marks else x.get("mark", "")
        riders.append(x)
    refs = {}
    item = sb.race_item(STATE["ds"], refs, venue=b.get("venue") or "", no=b.get("no") or 0,
                        cat=f"手入力 {b.get('category', '')}", category=b.get("category"), cls=b.get("cls"),
                        url=b.get("url") or "", lines=lines, riders=riders, exclude_id=b.get("rid"))
    item["manual"] = True
    return jsonify(item=item, refs=refs)


if __name__ == "__main__":
    prepare()
    port = 5000
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    print(f"ブラウザで http://localhost:{port} を開いてください（閉じるときはこの画面を閉じる）")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
