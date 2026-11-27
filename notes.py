"""競輪場の特徴・選手のS評価とメモ（notes/ 以下のテキスト）を notes/notes.json にまとめる。

notes/venues/<競輪場名>.md   … 中身をそのまま特徴メモとして表示
notes/riders/<選手キー>.md   … 「S評価: A」の行と、「メモ:」より下を読む
    選手キーは nk + netkeirinの選手ID（例 nk15163）。ページの「書き込む」から作ると自動で付きます。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
NOTES = ROOT / "notes"
NOTES_JSON = NOTES / "notes.json"
GRADES = ["S", "A", "B", "C", "D", "E"]


def parse_rider(text: str) -> dict:
    grade, memo, in_memo = None, [], False
    for line in text.splitlines():
        m = re.match(r"\s*S評価\s*[:：]\s*([SABCDEＳＡＢＣＤＥ])?", line)
        if m and not in_memo:
            g = (m.group(1) or "").translate(str.maketrans("ＳＡＢＣＤＥ", "SABCDE"))
            grade = g if g in GRADES else None
            continue
        if re.match(r"\s*メモ\s*[:：]", line) and not in_memo:
            in_memo = True
            rest = re.sub(r"\s*メモ\s*[:：]\s*", "", line, count=1)
            if rest:
                memo.append(rest)
            continue
        if in_memo:
            memo.append(line)
    return dict(grade=grade, memo="\n".join(memo).strip())


def compile_notes() -> dict:
    out = {"venues": {}, "riders": {}}
    for f in sorted((NOTES / "venues").glob("*.md")):
        body = "\n".join(l for l in f.read_text(encoding="utf-8").splitlines() if not l.startswith("# ")).strip()
        out["venues"][f.stem] = body
    for f in sorted((NOTES / "riders").glob("*.md")):
        out["riders"][f.stem] = parse_rider(f.read_text(encoding="utf-8"))
    NOTES.mkdir(exist_ok=True)
    NOTES_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    return out


if __name__ == "__main__":
    o = compile_notes()
    print(f"競輪場 {len(o['venues'])}件・選手 {len(o['riders'])}件")
