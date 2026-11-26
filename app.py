"""F1ライン分析アプリ。  python app.py → ブラウザで http://localhost:5000"""
from __future__ import annotations

import threading
import webbrowser
from datetime import date, timedelta

from flask import Flask, jsonify, request, send_from_directory

import analysis as an
import kd_scraper as kd
import store

app = Flask(__name__, static_folder="static")
con = store.connect()
_cache = {"count": -1, "ds": []}
_cache_lock = threading.Lock()


def races():
    """DBの件数が変わったら特徴量を作り直す（取得した結果がすぐ分析に反映される）"""
    n = store.race_count(con)
    with _cache_lock:
        if n != _cache["count"]:
            _cache["ds"] = an.derive_all(store.load_races(con))
            _cache["count"] = n
        return _cache["ds"]


def filters():
    return {k: request.args.get(k) for k in ("cls", "category", "type", "shape", "bank", "venue", "n_cars", "since")}


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/meta")
def meta():
    ds = races()
    vs = sorted({d["venue"] for d in ds}, key=lambda v: (an.BANKS.index(an.bank_of(v)), v))
    return jsonify(count=len(ds), raw=store.race_count(con), last=store.last_day(con), venues=vs,
                   oldest=min((d["date"] for d in ds), default=None),
                   categories=kd.CATEGORY_ORDER, banks=an.BANKS,
                   bank333=sorted(an.BANK_333), bank500=sorted(an.BANK_500),
                   types=["三分戦", "二分戦", "二分戦コマ切れ", "その他", "超コマ切れ"],
                   shapes=sorted({d["shape"] for d in ds}, key=lambda s: (-sum(map(int, s.split("-"))), s)))


@app.get("/api/stats")
def api_stats():
    return jsonify(an.stats(an.apply_filters(races(), filters())))


@app.get("/api/venues")
def api_venues():
    return jsonify(an.venues(an.apply_filters(races(), filters())))


@app.get("/api/today/races")
def today_races():
    d = date.fromisoformat(request.args.get("date") or date.today().isoformat())
    out = []
    for md in kd.list_day(d, "racecard"):
        if md.grade != "F1":
            continue
        out.append(dict(venue=md.venue, slug=md.venue_slug,
                        races=[dict(id=r, no=int(r[14:16])) for r in md.race_ids]))
    return jsonify(out)


@app.get("/api/today/race")
def today_race():
    r = kd.fetch_race(request.args["id"], request.args["slug"], result=False)
    if not r:
        return jsonify(error="出走表を読み取れませんでした"), 404
    return jsonify(r.to_dict())


@app.post("/api/match")
def api_match():
    b = request.get_json()
    lines = b.get("lines") or an.parse_lines_text(b.get("lines_text", ""))
    marks = {int(k): v for k, v in (b.get("marks") or {}).items()}
    res = an.match(races(), lines, marks, b.get("s_line"), category=b.get("category"),
                   cls=b.get("cls"), venue=b.get("venue"))
    return jsonify(res)


@app.post("/api/update")
def api_update():
    if store.progress.running:
        return jsonify(ok=False, message="取得中です")
    b = request.get_json() or {}
    end = date.fromisoformat(b["to"]) if b.get("to") else date.today()
    if b.get("from"):
        start = date.fromisoformat(b["from"])
    else:
        last = store.last_day(con)
        start = date.fromisoformat(last) - timedelta(days=1) if last else end - timedelta(days=30)

    def run():
        try:
            store.collect(start, end, store.connect())
        except Exception:
            pass
    store.progress.running = True
    store.progress.total = 0
    store.progress.done = 0
    store.progress.message = "開始します"
    threading.Thread(target=run, daemon=True).start()
    return jsonify(ok=True, start=start.isoformat(), end=end.isoformat())


@app.get("/api/progress")
def api_progress():
    return jsonify(store.progress.as_dict())


if __name__ == "__main__":
    import sys
    port = 5000
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
