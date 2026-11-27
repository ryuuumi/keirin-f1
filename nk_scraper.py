"""netkeirin（keirin.netkeiba.com）の出走表から、選手ID と 直近4か月の S・H・B・決まり手回数を取る。

出走表: https://keirin.netkeiba.com/race/entry/?race_id=YYYYMMDD + 場コード2 + レース番号2
  （場コードはKドリームスと同じ。日付は開催初日ではなく、そのレースの実施日）
  table#RaceCard_Table_Fixed の tr.PlayerList が1選手。列の並び：
  枠 / 車 / 印 / 予想 / 選手名(a.Player01 id="names_<選手ID>_<race_id>") / 競走得点 / 脚質 /
  S / H / B / 逃げ / まくり / 差し / マーク / 1着 / 2着 / 3着 / 着外 / 勝率 / 2連対率 / 3連対率 / ギヤ / コメント
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

import kd_scraper as kd

BASE = "https://keirin.netkeiba.com"
FIELDS = ["s", "h", "b", "nige", "makuri", "sashi", "mark", "r1", "r2", "r3", "rx"]


def race_id(run_date: str, venue_code: str, race_no: int) -> str:
    return run_date.replace("-", "") + venue_code + f"{race_no:02d}"


def fetch_stats(run_date: str, venue_code: str, race_no: int) -> dict[int, dict]:
    """{車番: {nk_id, s, h, b, nige, makuri, sashi, mark, r1, r2, r3, rx}}。取れなければ空。"""
    html = kd.fetch(f"{BASE}/race/entry/?race_id={race_id(run_date, venue_code, race_no)}")
    if not html:
        return {}
    soup = BeautifulSoup(html, "lxml")
    tbl = soup.find("table", id="RaceCard_Table_Fixed")
    if not tbl:
        return {}
    out = {}
    for tr in tbl.find_all("tr", class_="PlayerList"):
        tds = tr.find_all("td")
        a = tr.find("a", class_="Player01")
        if len(tds) < 18 or not a:
            continue
        try:
            car = int(tds[1].get_text(strip=True))
        except ValueError:
            continue
        m = re.match(r"names_(\d+)_", a.get("id", ""))
        nums = []
        for td in tds[7:18]:
            t = td.get_text(strip=True)
            nums.append(int(t) if t.isdigit() else 0)
        d = dict(zip(FIELDS, nums))
        d["nk_id"] = m.group(1) if m else None
        out[car] = d
    return out


def enrich(race: "kd.Race") -> bool:
    """Kドリームスのレースに netkeirin の値を足す。足せたら True。"""
    try:
        st = fetch_stats(race.date, race.race_id[:2], race.race_no)
    except Exception:
        return False
    if not st:
        return False
    for r in race.riders:
        x = st.get(r.car)
        if x:
            r.nk = x
    return True


def enrich_dict(race: dict) -> bool:
    """保存済み（辞書）のレースに netkeirin の値を足す。"""
    try:
        st = fetch_stats(race["date"], race["race_id"][:2], race["race_no"])
    except Exception:
        return False
    if not st:
        race["nk_checked"] = True
        return False
    for r in race["riders"]:
        x = st.get(r["car"])
        if x:
            r["nk"] = x
    race["nk_checked"] = True
    return True
