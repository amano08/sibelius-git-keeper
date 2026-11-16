#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sibelius-git-keeper 監視 (保険)

プラグインを経由しない保存 (ツールバーの保存ボタンなど) を取りこぼさない
ための常駐ポーリング監視。FSEvents の代わりにポーリングで実装している
(試作。fswatch 等の外部依存なし)。

- 対象: 設定ファイルに記録された repo 内で、曲フォルダ (直下に .sib が
  あるフォルダ、予約名フォルダ配下を除く) にある .sib 本体のみ
- .sib の変更を検知したら、一定時間 (quiet) 書き込みが落ち着くのを
  待ってから keeper.py save を呼ぶ
- git 操作はすべて keeper.py に集約し、ロックも共有される
- pull は行わない。譜面ファイルを書き換えることもない
- ついでに Cmd+S の割り当て状態マーカー (cmds_state /
  cmds_unassigned.flag) を維持する。判定は keeper 側の読み取り専用
  ロジックに寄せ、ここでは定期的に呼び直すだけにする

使い方:
  watcher.py [--config <path>] [--interval <sec>] [--quiet <sec>]
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import keeper as keeper_mod  # noqa: E402


def scan_sib_files(repo):
    """repo 内の管轄対象になり得る .sib の {path: (mtime, size)} を返す。"""
    found = {}
    for dirpath, dirnames, filenames in os.walk(repo):
        # 予約フォルダと .git には入らない
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(("_", "."))]
        for name in filenames:
            if name.lower().endswith(".sib"):
                path = os.path.join(dirpath, name)
                inside, _reason = keeper_mod.jurisdiction(repo, path)
                if inside:
                    try:
                        st = os.stat(path)
                        found[path] = (st.st_mtime, st.st_size)
                    except OSError:
                        pass
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(prog="watcher.py")
    parser.add_argument("--config", default=None)
    parser.add_argument("--interval", type=float, default=5.0,
                        help="ポーリング間隔 (秒)")
    parser.add_argument("--quiet", type=float, default=8.0,
                        help="変更後、書き込みが落ち着くまで待つ秒数")
    args = parser.parse_args(argv)

    kp = keeper_mod.Keeper(args.config or keeper_mod.default_config_path())
    repo = kp.repo
    if repo is None:
        print("watcher: repo is not configured; exiting", flush=True)
        return 3

    keeper_py = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "keeper.py")
    config_args = ["--config", kp.config_path] if args.config else []

    print("watcher: watching %s (interval=%ss quiet=%ss)"
          % (repo, args.interval, args.quiet), flush=True)

    snapshot = scan_sib_files(repo)
    pending = {}  # path -> 変更を最後に検知した時刻

    last_shortcut_state = None
    try:
        last_shortcut_state, _ev = keeper_mod.update_shortcut_marker(kp)
        print("watcher: Cmd+S state: %s" % last_shortcut_state, flush=True)
    except Exception as e:
        print("watcher: shortcut marker update failed: %s" % e, flush=True)

    while True:
        time.sleep(args.interval)
        try:
            state_now, _ev = keeper_mod.update_shortcut_marker(kp)
            if state_now != last_shortcut_state:
                print("watcher: Cmd+S state: %s -> %s"
                      % (last_shortcut_state, state_now), flush=True)
                last_shortcut_state = state_now
        except Exception as e:
            print("watcher: shortcut marker update failed: %s" % e,
                  flush=True)
        current = scan_sib_files(repo)
        now = time.time()
        for path, sig in current.items():
            if path not in snapshot or snapshot[path] != sig:
                pending[path] = now
        for path in list(pending):
            if path not in current:
                del pending[path]
        snapshot = current
        for path, since in list(pending.items()):
            if now - since >= args.quiet:
                del pending[path]
                cmd = ([sys.executable, keeper_py] + config_args
                       + ["save", "--file", path])
                print("watcher: save detected -> %s" % path, flush=True)
                try:
                    p = subprocess.run(cmd, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, timeout=600)
                    out = p.stdout.decode("utf-8", "replace").strip()
                    print("watcher: keeper rc=%d %s" % (p.returncode, out),
                          flush=True)
                except (OSError, subprocess.TimeoutExpired) as e:
                    print("watcher: keeper call failed: %s" % e, flush=True)


if __name__ == "__main__":
    sys.exit(main())
