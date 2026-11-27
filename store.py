"""SQLite への保存と、期間を指定した F1 レースの一括取得。"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import kd_scraper as kd
import nk_scraper as nk

DB_PATH = Path(__file__).with_name("data") / "keirin.sqlite3"
_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("""CREATE TABLE IF NOT EXISTS races(
        race_id TEXT PRIMARY KEY, venue_slug TEXT, venue TEXT, date TEXT,
        grade TEXT, category TEXT, cls TEXT, has_result INTEGER,
        data TEXT, fetched_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS scanned_days(
        day TEXT PRIMARY KEY, scanned_at TEXT)""")
    return con


def save_race(con, r: kd.Race):
    with _lock:
        con.execute("INSERT OR REPLACE INTO races VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (r.race_id, r.venue_slug, r.venue, r.date, r.grade, r.category, r.cls,
                     int(r.has_result), json.dumps(r.to_dict(), ensure_ascii=False),
                     datetime.now().isoformat(timespec="seconds")))
        con.commit()


def load_races(con, only_result=True) -> list[dict]:
    q = "SELECT data FROM races" + (" WHERE has_result=1" if only_result else "")
    with _lock:
        return [json.loads(row[0]) for row in con.execute(q)]


def known_ids(con) -> set[str]:
    with _lock:
        return {r[0] for r in con.execute("SELECT race_id FROM races WHERE has_result=1")}


def race_count(con) -> int:
    with _lock:
        return con.execute("SELECT COUNT(*) FROM races WHERE has_result=1").fetchone()[0]


def last_day(con) -> str | None:
    with _lock:
        return con.execute("SELECT MAX(date) FROM races WHERE has_result=1").fetchone()[0]


# ------------------------------------------------------------ 日付ごとのJSONファイル（GitHub保存用）
RACES_DIR = Path(__file__).with_name("races")


def dump_jsonl(con, dates=None) -> int:
    """DBの内容を races/YYYY/MM/DD.jsonl に書き出す。dates を渡すとその日だけ。"""
    by_day: dict[str, list] = {}
    for r in load_races(con):
        if dates is None or r["date"] in dates:
            by_day.setdefault(r["date"], []).append(r)
    for day, rs in by_day.items():
        f = RACES_DIR / day[:4] / day[5:7] / f"{day[8:]}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        rs.sort(key=lambda r: r["race_id"])
        f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rs), encoding="utf-8")
    return len(by_day)


def load_jsonl(con) -> int:
    """races/ 以下のファイルをDBに読み込む（取得済みのレースは上書き）"""
    n = 0
    rows = []
    for f in sorted(RACES_DIR.glob("*/*/*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            rows.append((r["race_id"], r["venue_slug"], r["venue"], r["date"], r.get("grade"), r["category"],
                         r["cls"], int(r["has_result"]), line, datetime.now().isoformat(timespec="seconds")))
            n += 1
    with _lock:
        con.executemany("INSERT OR REPLACE INTO races VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
    return n


def first_day(con) -> str | None:
    with _lock:
        return con.execute("SELECT MIN(date) FROM races WHERE has_result=1").fetchone()[0]


def enrich_missing(con, deadline=None, log=print) -> set[str]:
    """netkeirin の値がまだないレースに足す（新しい順）。足した日の集合を返す。"""
    import time as _t
    import nk_scraper as nk
    # nk_v=2 … 府県・期別まで取り込み済み
    todo = [r for r in load_races(con) if r.get("cls") == "S級" and r.get("nk_v") != 2
            and not any((x.get("nk") or {}).get("pref") for x in r["riders"])]
    todo.sort(key=lambda r: r["date"], reverse=True)
    days = set()
    done = 0
    for i, r in enumerate(todo):
        if deadline and _t.time() > deadline:
            break
        done += 1
        nk.enrich_dict(r)
        r["url"] = r.get("url")
        with _lock:
            con.execute("UPDATE races SET data=? WHERE race_id=?", (json.dumps(r, ensure_ascii=False), r["race_id"]))
            if i % 50 == 0:
                con.commit()
        days.add(r["date"])
    with _lock:
        con.commit()
    log(f"netkeirin の値を {done} レースに追加（残り {len(todo) - done} レース）")
    return days


# ------------------------------------------------------------ 一括取得
class Progress:
    def __init__(self):
        self.running = False
        self.message = ""
        self.done = 0
        self.total = 0
        self.added = 0
        self.error = ""

    def as_dict(self):
        return dict(running=self.running, message=self.message, done=self.done,
                    total=self.total, added=self.added, error=self.error)


progress = Progress()


def collect(start: date, end: date, con=None, grades=("F1",), log=print, deadline=None, newest_first=False):
    """start〜end の結果を取得して保存。取得済みのレースは飛ばす。
    deadline（time.time() の値）を過ぎたらそこで止める。"""
    import time as _t
    con = con or connect()
    have = known_ids(con)
    progress.running, progress.added, progress.error = True, 0, ""
    try:
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        if newest_first:
            days.reverse()
        targets = []
        for i, d in enumerate(days):
            if deadline and _t.time() > deadline:
                break
            progress.message = f"{d:%Y/%m/%d} の開催を確認中（{i + 1}/{len(days)}日）"
            for md in kd.list_day(d, "raceresult"):
                if md.grade not in grades:
                    continue
                todo = [rid for rid in md.race_ids if rid not in have]
                if not todo:
                    continue
                try:  # S級のレースだけにしぼる（一覧ページ1枚で級が分かる）
                    cls = kd.meeting_classes(md.venue_slug, md.day_id, "raceresult")
                except Exception:
                    cls = {}
                for rid in todo:
                    if cls and not kd.is_target(cls.get(rid, "")):
                        continue
                    targets.append((rid, md.venue_slug, md.venue))
        progress.total, progress.done = len(targets), 0
        for rid, slug, venue in targets:
            if deadline and _t.time() > deadline:
                log("時間切れのため途中で止めます")
                break
            progress.message = f"{venue} {rid[14:16]}R（{rid[2:6]}/{rid[6:8]}/{rid[8:10]}開催 {int(rid[10:12])}日目）"
            try:
                r = kd.fetch_race(rid, slug)
                if r and r.has_result:
                    nk.enrich(r)
                    save_race(con, r)
                    progress.added += 1
            except Exception as e:  # 1レースの失敗で止めない
                log(f"skip {rid}: {e}")
            progress.done += 1
        progress.message = f"完了：{progress.added}レース追加"
    except Exception as e:
        progress.error = str(e)
        progress.message = "エラーで停止しました"
        raise
    finally:
        progress.running = False
    return progress.added


if __name__ == "__main__":
    import argparse
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "dump":
        print(f"{dump_jsonl(connect())}日分を races/ に書き出しました")
        raise SystemExit
    if len(sys.argv) > 1 and sys.argv[1] == "load":
        print(f"{load_jsonl(connect())}レースを読み込みました")
        raise SystemExit
    ap = argparse.ArgumentParser(description="KドリームスからF1戦の結果を取得")
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="end", default=None, help="YYYY-MM-DD（省略時は昨日）")
    a = ap.parse_args()
    s = date.fromisoformat(a.start)
    e = date.fromisoformat(a.end) if a.end else date.today() - timedelta(days=1)

    import threading as th, time

    def show():
        while progress.running or progress.total == 0:
            print(f"\r{progress.message}  {progress.done}/{progress.total}      ", end="", flush=True)
            time.sleep(2)
            if not progress.running and progress.message.startswith(("完了", "エラー")):
                break
    progress.running = True
    t = th.Thread(target=show, daemon=True)
    t.start()
    n = collect(s, e, log=lambda m: None)
    print(f"\n{n}レース追加。合計 {race_count(connect())} レース")
