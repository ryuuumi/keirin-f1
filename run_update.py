"""GitHub Actions（またはPC）で動かす更新処理。

  python run_update.py daily            … 直近の結果を取り込み、今日・明日のページを書き出す
  python run_update.py backfill --minutes 300 --since 2025-12-01
                                        … 過去の結果をさかのぼって取り込む（時間で区切って続きから）
"""
from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta, timezone

import analysis as an
import site_builder
import store

JST = timezone(timedelta(hours=9))


def today_jst() -> date:
    return datetime.now(JST).date()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["daily", "backfill", "site"])
    ap.add_argument("--minutes", type=float, default=300)
    ap.add_argument("--since", default=None, help="backfill でさかのぼる最古の日 YYYY-MM-DD")
    a = ap.parse_args()

    con = store.connect()
    n = store.load_jsonl(con)
    print(f"保存済み {n} レースを読み込み")
    deadline = time.time() + a.minutes * 60
    today = today_jst()

    if a.mode == "daily":
        last = store.last_day(con)
        start = date.fromisoformat(last) - timedelta(days=1) if last else today - timedelta(days=7)
        store.collect(start, today, con, log=print, deadline=deadline)
        days = sorted({r["date"] for r in store.load_races(con) if r["date"] >= start.isoformat()})
        store.dump_jsonl(con, set(days))
        print(f"{store.progress.added} レース追加")
        mode_site = True
    elif a.mode == "backfill":
        since = date.fromisoformat(a.since) if a.since else today - timedelta(days=365)
        first = store.first_day(con)
        # 最古の日は途中で止まっている可能性があるので含めて見直す（取得済みは飛ばす）
        end = date.fromisoformat(first) if first else today - timedelta(days=1)
        # まず取り込み済みのレースに netkeirin の値（H・B回数、選手ID）を足す
        enriched = store.enrich_missing(con, deadline=deadline if end < since else time.time() + a.minutes * 30)
        store.dump_jsonl(con, enriched)
        if end < since:
            print("さかのぼる日はもうありません")
            return
        before = {r["race_id"] for r in store.load_races(con)}
        # 新しい日から古い日へ。開催一覧の確認も時間内に収める
        chunk_start = max(since, end - timedelta(days=30))
        while time.time() < deadline and end >= since:
            store.collect(chunk_start, end, con, log=print, deadline=deadline, newest_first=True)
            if time.time() >= deadline:
                break
            end = chunk_start - timedelta(days=1)
            chunk_start = max(since, end - timedelta(days=30))
        new_days = {r["date"] for r in store.load_races(con) if r["race_id"] not in before}
        store.dump_jsonl(con, new_days)
        print(f"{len(new_days)} 日分を追加")
        mode_site = False
    else:
        mode_site = True

    if mode_site:
        raw = store.load_races(con)
        ds = an.derive_all(raw)
        site_builder.build_site(ds, [today, today + timedelta(days=1)], races_raw={r["race_id"]: r for r in raw})
        print("ページを書き出しました")


if __name__ == "__main__":
    main()
