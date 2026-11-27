"""PCアプリ用：GitHub から最新のプログラム・取り込み済みの結果・メモをダウンロードして上書きする。
start.bat / start.command が起動のたびに最初に実行する。ネットにつながらないときは何もしない。
"""
from __future__ import annotations

import io
import shutil
import sys
import zipfile
from pathlib import Path

import requests

REPO = "ryuuumi/keirin-f1"
ROOT = Path(__file__).parent
KEEP = {"data", ".venv", ".git", "site", "__pycache__"}   # 上書きしないもの


def main():
    url = f"https://codeload.github.com/{REPO}/zip/refs/heads/main"
    print("GitHubから最新を取り込んでいます…")
    try:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
    except Exception as e:
        print(f"取り込めませんでした（前回の内容のまま起動します）：{e}")
        return
    z = zipfile.ZipFile(io.BytesIO(r.content))
    top = z.namelist()[0].split("/")[0] + "/"
    n = 0
    for info in z.infolist():
        if info.is_dir() or not info.filename.startswith(top):
            continue
        rel = info.filename[len(top):]
        if not rel or rel.split("/")[0] in KEEP:
            continue
        dest = ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with z.open(info) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
        n += 1
    print(f"{n}ファイルを更新しました")


if __name__ == "__main__":
    main()
    sys.exit(0)
