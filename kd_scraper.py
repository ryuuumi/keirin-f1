"""楽天Kドリームスから F1 戦の出走表・結果を取得して解析する。

ページ構造（2026/11 時点で確認）:
  日付ごとの結果一覧 : https://keirin.kdreams.jp/raceresult/YYYY/MM/DD/
  日付ごとの出走表一覧: https://keirin.kdreams.jp/racecard/YYYY/MM/DD/
    div.raceinfo_table の各行に 競輪場 / グレード(span.icon_grade gr2 = F1) / レースリンク
  レース詳細          : https://keirin.kdreams.jp/{場}/racedetail/{16桁ID}/?pageType=result
    16桁ID = 場コード2 + 開催初日YYYYMMDD + 日目2 + 00 + レース番号2
"""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import date

import requests
from bs4 import BeautifulSoup

BASE = "https://keirin.kdreams.jp"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
}
WAIT_SEC = 1.0          # サイトに負荷をかけないよう 1 リクエストごとに待つ
F1_GRADE_CLASS = "gr2"  # 一覧ページのグレードアイコン。gr2 = F1（gr1 = F2）

_session = requests.Session()
_session.headers.update(HEADERS)
_last = 0.0


def fetch(url: str) -> str:
    global _last
    wait = WAIT_SEC - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    for i in range(3):
        try:
            r = _session.get(url, timeout=20)
            _last = time.time()
            if r.status_code == 200:
                r.encoding = "utf-8"
                return r.text
            if r.status_code == 404:
                return ""
        except requests.RequestException:
            pass
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"取得できませんでした: {url}")


def nfkc(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip()


# ---------------------------------------------------------------- 日付一覧
@dataclass
class MeetingDay:
    venue_slug: str
    venue: str
    grade: str          # "F1" / "F2" / "other"
    day_id: str         # 14桁
    race_ids: list[str]


def list_day(d: date, kind: str = "raceresult") -> list[MeetingDay]:
    """kind = raceresult（結果）/ racecard（出走表）"""
    html = fetch(f"{BASE}/{kind}/{d:%Y/%m/%d}/")
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tr in soup.select("div.raceinfo_table tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        g = tds[1].find("span", class_=re.compile("icon_grade"))
        gcls = [c for c in (g.get("class") if g else []) if c.startswith("gr")]
        grade = {"gr2": "F1", "gr1": "F2"}.get(gcls[0] if gcls else "", "other")
        race_ids, day_id, slug = [], "", ""
        for a in tds[3].find_all("a", href=True):
            m = re.search(r"/([a-z]+)/racedetail/(\d{16})/", a["href"])
            if m:
                slug = m.group(1)
                race_ids.append(m.group(2))
            m2 = re.search(r"/([a-z]+)/(?:raceresult|racecard)/(\d{14})/", a["href"])
            if m2:
                slug, day_id = m2.group(1), m2.group(2)
        if race_ids:
            out.append(MeetingDay(slug, nfkc(tds[0].get_text()).replace("競輪", ""),
                                  grade, day_id or race_ids[0][:14], sorted(set(race_ids))))
    return out


def meeting_classes(slug: str, day_id: str, kind: str = "raceresult") -> dict[str, str]:
    """開催日ごとの一覧ページから {レースID: 種別名（例 'S級予選'）}。kind = raceresult / racecard"""
    html = fetch(f"{BASE}/{slug}/{kind}/{day_id}/")
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for p in soup.select("p.race"):
        a = p.find("a", href=True)
        name = p.find("span", class_="name")
        m = re.search(r"racedetail/(\d{16})", a["href"]) if a else None
        if m and name:
            out[m.group(1)] = nfkc(name.get_text())
    return out


def is_target(category_raw: str) -> bool:
    """分析の対象：S級のレースだけ（A級・チャレンジ・ガールズは取り込まない）"""
    return normalize_category(category_raw)[0] == "S級"


# ---------------------------------------------------------------- レース詳細
@dataclass
class Rider:
    car: int
    name: str
    score: float | None = None
    mark: str = ""          # アオケイ記者の予想印 ◎○▲△×注
    style: str = ""         # 脚質 逃/両/追
    rank: int | None = None  # 着順（失格・落車などは None）
    rank_text: str = ""
    sb: str = ""            # S / B
    kimarite: str = ""      # 逃/捲/差/マ
    agari: str = ""
    pref: str = ""          # 府県
    age: int | None = None
    term: int | None = None  # 期別
    klass: str = ""         # 級班 A1 など
    nk: dict | None = None  # netkeirin の選手ID と 直近4か月の S/H/B/決まり手回数


@dataclass
class Race:
    race_id: str
    venue_slug: str
    venue: str
    date: str               # 実施日 YYYY-MM-DD
    day_no: int             # 何日目
    race_no: int
    grade: str              # F1 など
    cls: str                # S級 / A級 / チャレンジ / ガールズ
    category: str           # 予選 / 初日特選 / 準決勝 / 一般 / 選抜 / 特選 / 決勝 / その他
    category_raw: str
    title: str
    lines: list[list[int]] = field(default_factory=list)   # 並び（前から）
    riders: list[Rider] = field(default_factory=list)
    payouts: dict = field(default_factory=dict)            # {"3連単": {"combo": "7-2-6", "yen": 79720, "pop": 163}, ...}
    has_result: bool = False
    bank: int | None = None  # 周長（m）

    @property
    def url(self) -> str:
        return f"{BASE}/{self.venue_slug}/racedetail/{self.race_id}/?pageType=result"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["url"] = self.url
        return d


CATEGORY_ORDER = ["予選", "初日特選", "準決勝", "一般", "選抜", "特選", "決勝", "その他"]


def normalize_category(raw: str) -> tuple[str, str]:
    s = nfkc(raw)
    if "ガールズ" in s or s.startswith("L級"):
        cls = "ガールズ"
    elif "チャレンジ" in s:
        cls = "チャレンジ"
    elif s.startswith("S級"):
        cls = "S級"
    elif s.startswith("A級"):
        cls = "A級"
    else:
        cls = "不明"
    if "初日特" in s or "初特" in s:
        cat = "初日特選"
    elif "準決" in s:
        cat = "準決勝"
    elif "決勝" in s:
        cat = "決勝"
    elif "予選" in s:
        cat = "予選"
    elif "特選" in s or "特別選抜" in s:
        cat = "特選"
    elif "選抜" in s:
        cat = "選抜"
    elif "一般" in s:
        cat = "一般"
    else:
        cat = "その他"
    return cls, cat


def _yen(s: str) -> int | None:
    m = re.search(r"([\d,]+)円", s)
    return int(m.group(1).replace(",", "")) if m else None


def _pop(s: str) -> int | None:
    m = re.search(r"\((\d+)\)", s)
    return int(m.group(1)) if m else None


def parse_lines(soup: BeautifulSoup) -> list[list[int]]:
    box = soup.find("div", class_="line_position")
    if not box:
        return []
    lines, cur = [], []
    for sp in box.find_all("span", class_="icon_p", recursive=False):
        if "space" in (sp.get("class") or []):
            if cur:
                lines.append(cur)
                cur = []
            continue
        num = sp.find("span", class_=re.compile(r"^p00[1-9]$"))
        if num:
            t = nfkc(num.get_text())
            if t.isdigit():
                cur.append(int(t))
    if cur:
        lines.append(cur)
    return lines


def parse_payouts(soup: BeautifulSoup) -> dict:
    t = soup.find("table", class_="refund_table")
    if not t:
        return {}
    rows = t.find_all("tr")
    res: dict = {}

    def entry(dl):
        dt, dd = dl.find("dt"), dl.find("dd")
        if not dt or not dd:
            return None
        return {"combo": nfkc(dt.get_text()), "yen": _yen(dd.get_text()), "pop": _pop(dd.get_text())}

    if rows:
        wide = rows[0].find("td", class_="wide")
        normal = [td for td in rows[0].find_all("td") if td is not wide and td.find("dl")]
        for name, td in zip(["2枠複", "2車複", "3連複"], normal):
            e = entry(td.find("dl"))
            if e and e["yen"]:
                res[name] = e
        if wide:
            res["ワイド"] = [e for e in (entry(dl) for dl in wide.find_all("dl")) if e and e["yen"]]
    if len(rows) > 1:
        tds = [td for td in rows[1].find_all("td") if td.find("dl")]
        for name, td in zip(["2枠単", "2車単", "3連単"], tds):
            e = entry(td.find("dl"))
            if e and e["yen"]:
                res[name] = e
    return res


def parse_race(html: str, race_id: str, venue_slug: str) -> Race | None:
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    h2 = soup.find("h2", class_="title")
    status = h2.find("span", class_="status") if h2 else None
    if not status:
        return None
    category_raw = nfkc(status.get_text())
    title = nfkc(h2.get_text()).replace(category_raw, "").strip()
    cls, cat = normalize_category(category_raw)

    info = soup.find("p", class_="raceinfo_contents-title")
    info_t = nfkc(info.get_text()) if info else ""
    grade = info_t.split(" ")[0] if info_t else ""
    h1 = soup.find("h1", class_="raceinfo_headline")
    venue = nfkc(h1.get_text()).split(" ")[0].replace("競輪", "") if h1 else venue_slug

    start = date(int(race_id[2:6]), int(race_id[6:8]), int(race_id[8:10]))
    day_no = int(race_id[10:12])
    from datetime import timedelta
    run_date = start + timedelta(days=day_no - 1)

    race = Race(race_id=race_id, venue_slug=venue_slug, venue=venue, date=run_date.isoformat(),
                day_no=day_no, race_no=int(race_id[14:16]), grade=grade, cls=cls, category=cat,
                category_raw=category_raw, title=title)

    # 出走表（1つ目の racecard_table）: 予想印・脚質・競走得点
    card = soup.find("table", class_="racecard_table")
    riders: dict[int, Rider] = {}
    if card:
        for tr in card.find_all("tr", class_=re.compile(r"^n\d$")):
            num = tr.find("td", class_="num")
            rider = tr.find("td", class_="rider")
            if not num or not rider:
                continue
            car = int(nfkc(num.get_text()))
            name = nfkc(rider.contents[0] if rider.contents else rider.get_text())
            tip = tr.find("td", class_="tip")
            r = Rider(car=car, name=name, mark=nfkc(tip.get_text()) if tip else "")
            home = rider.find("span", class_="home")
            if home:
                parts = nfkc(home.get_text()).replace(" ", "").split("/")
                if len(parts) >= 3:
                    r.pref = parts[0]
                    r.age = int(parts[1]) if parts[1].isdigit() else None
                    r.term = int(parts[2]) if parts[2].isdigit() else None
            after = rider.find_next_siblings("td")
            texts = [nfkc(td.get_text()) for td in after]
            if texts:
                r.klass = texts[0]
            if len(texts) > 1:
                r.style = texts[1]
            for tx in texts:
                if re.fullmatch(r"\d{2,3}\.\d{1,2}", tx):
                    r.score = float(tx)
                    break
            riders[car] = r

    # 結果
    rt = soup.find("table", class_="result_table")
    if rt:
        for tr in rt.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) < 8:
                continue
            num = tds[2].get_text()
            num = nfkc(num)
            if not num.isdigit():
                continue
            car = int(num)
            r = riders.get(car) or Rider(car=car, name=nfkc(tds[3].get_text()))
            if not r.mark:
                r.mark = nfkc(tds[0].get_text())
            rk = nfkc(tds[1].get_text())
            r.rank_text = rk
            r.rank = int(rk) if rk.isdigit() else None
            r.agari = nfkc(tds[5].get_text())
            r.kimarite = nfkc(tds[6].get_text())
            r.sb = nfkc(tds[7].get_text())
            riders[car] = r
        race.has_result = any(r.rank for r in riders.values())

    race.riders = [riders[k] for k in sorted(riders)]
    mb = re.search(r"同走路年間勝利度数【(\d+)】", html)
    race.bank = int(mb.group(1)) if mb else None
    race.lines = parse_lines(soup)
    race.payouts = parse_payouts(soup) if race.has_result else {}
    return race


def fetch_race(race_id: str, venue_slug: str, result: bool = True) -> Race | None:
    url = f"{BASE}/{venue_slug}/racedetail/{race_id}/"
    if result:
        url += "?pageType=result"
    return parse_race(fetch(url), race_id, venue_slug)


if __name__ == "__main__":
    import json, sys
    rid, slug = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else ("4720261126010005", "matsusaka")
    r = fetch_race(rid, slug)
    print(json.dumps(r.to_dict() if r else None, ensure_ascii=False, indent=1))
