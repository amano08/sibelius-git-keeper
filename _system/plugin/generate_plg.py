#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
.plg の生成スクリプト。

src/*.plg.txt (UTF-8 のソース) から、同階層に *.plg を生成する。
符号化は UTF-16BE + BOM。

生成した .plg には __REPO_ROOT__ / __HOME__ のプレースホルダが残る。
setup.sh が配置時に実際の値へ置換してから Plugins フォルダへ入れる。
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    made = []
    for src in sorted(glob.glob(os.path.join(HERE, "src", "*.plg.txt"))):
        with open(src, "r", encoding="utf-8") as f:
            text = f.read()
        dst = os.path.join(HERE, os.path.basename(src)[:-len(".plg.txt")] + ".plg")
        with open(dst, "wb") as f:
            f.write(b"\xfe\xff" + text.encode("utf-16-be"))
        made.append(dst)
    for path in made:
        print("generated: %s (%d bytes)" % (path, os.path.getsize(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
