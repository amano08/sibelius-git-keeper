#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sibelius-git-keeper: ヘルパースクリプト

Sibelius の譜面ファイル (.sib) の保存に合わせて、対象 repo へ
commit / push を行う。ManuScript プラグインと監視 (watcher) の
両方から呼ばれる。Python 3 標準ライブラリのみで動作する。

サブコマンド:
  save   --file <path>            保存された .sib を commit / push する
  status --file <path> [--open]   管轄内外、未送信数、サイズ、最終 push 等を表示
  config --repo <path>            対象 repo を設定ファイルへ記録する
  config --show                   現在の設定を表示する
  flag   --name <name> --on|--off 管轄外メッセージ抑制などのフラグを操作する
  check-shortcut                  Cmd+S の割り当て状態を判定し、マーカーを更新する

グローバルオプション:
  --config <path>  設定ファイルの場所 (既定: ~/.config/sibelius-git-keeper/config.json)

終了コード:
  0  正常 (commit した / 変更なし / push 済み)
  1  その他のエラー
  2  管轄外 (何もしない)
  3  未設定 (設定ファイルが無い、または repo が存在しない)
  4  競合 (remote が新しい。force push はしない。ローカル commit は保全済み)
  5  push 失敗 (ローカル commit は保持済み。未送信として記録される)
"""

import argparse
import datetime
import json
import os
import plistlib
import re
import subprocess
import sys
import time

DEFAULT_GITIGNORE = "*\n!*.sib\n!.gitignore\n"
LOCK_WAIT_SECONDS = 180          # ロック待ちの上限
LOCK_STALE_SECONDS = 600         # これより古く所有者が生存していないロックは破棄
GIT_TIMEOUT = 120                # git コマンド1回の上限秒数
RESERVED_PREFIXES = ("_", ".")


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def default_config_path():
    return os.path.join(os.path.expanduser("~"), ".config",
                        "sibelius-git-keeper", "config.json")


# ---------------------------------------------------------------- 設定と状態

class Keeper:
    def __init__(self, config_path):
        self.config_path = os.path.abspath(config_path)
        self.config_dir = os.path.dirname(self.config_path)
        self.state_path = os.path.join(self.config_dir, "state.json")
        self.config = self._load_json(self.config_path, {})
        self.state = self._load_json(self.state_path, {})

    @staticmethod
    def _load_json(path, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else default
        except (OSError, ValueError):
            return default

    def _save_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)

    def save_state(self):
        self._save_json(self.state_path, self.state)

    @property
    def repo(self):
        repo = self.config.get("repo")
        if not repo:
            return None
        repo = os.path.abspath(os.path.expanduser(repo))
        return repo if os.path.isdir(repo) else None


# ---------------------------------------------------------------- git 実行

def run_git(repo, args, timeout=GIT_TIMEOUT):
    """git を実行し (returncode, stdout, stderr) を返す。"""
    try:
        p = subprocess.run(
            ["git", "-C", repo] + args,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout)
        return (p.returncode,
                p.stdout.decode("utf-8", "replace"),
                p.stderr.decode("utf-8", "replace"))
    except subprocess.TimeoutExpired:
        return (124, "", "git command timed out: %s" % " ".join(args))
    except OSError as e:
        return (127, "", "git not runnable: %s" % e)


def unpushed_count(repo):
    """未送信 commit 数。upstream があればそれ基準、無ければ remote 未到達分。"""
    rc, out, _ = run_git(repo, ["rev-list", "--count", "@{upstream}..HEAD"])
    if rc == 0:
        return int(out.strip() or "0")
    rc, out, _ = run_git(repo, ["rev-list", "--count", "HEAD", "--not", "--remotes"])
    if rc == 0:
        return int(out.strip() or "0")
    return 0


def repo_size_bytes(repo):
    total = 0
    for dirpath, _dirnames, filenames in os.walk(repo):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def fmt_size(n):
    if n >= 1024 * 1024 * 1024:
        return "%.2f GB" % (n / float(1024 * 1024 * 1024))
    if n >= 1024 * 1024:
        return "%.1f MB" % (n / float(1024 * 1024))
    if n >= 1024:
        return "%.1f KB" % (n / float(1024))
    return "%d B" % n


# ---------------------------------------------------------------- ロック

class RepoLock:
    """repo 単位の排他ロック。.git/keeper.lock を使う。"""

    def __init__(self, repo):
        self.repo = repo
        self.path = os.path.join(repo, ".git", "keeper.lock")
        self.acquired = False

    def acquire(self):
        deadline = time.time() + LOCK_WAIT_SECONDS
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, ("%d %s\n" % (os.getpid(), now_iso())).encode())
                os.close(fd)
                self.acquired = True
                return True
            except FileExistsError:
                if self._is_stale():
                    try:
                        os.unlink(self.path)
                    except OSError:
                        pass
                    continue
                if time.time() >= deadline:
                    return False
                time.sleep(0.5)
            except OSError:
                return False

    def _is_stale(self):
        try:
            st = os.stat(self.path)
            if time.time() - st.st_mtime < LOCK_STALE_SECONDS:
                # 所有プロセスが生きているか
                with open(self.path, "r", encoding="utf-8") as f:
                    pid = int(f.read().split()[0])
                if pid == os.getpid():
                    return False
                try:
                    os.kill(pid, 0)
                    return False  # 生存中
                except OSError:
                    return True   # 所有者がいない
            return True
        except (OSError, ValueError, IndexError):
            return False

    def release(self):
        if self.acquired:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            self.acquired = False

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("could not acquire repo lock: %s" % self.path)
        return self

    def __exit__(self, *exc):
        self.release()
        return False


# ---------------------------------------------------------------- 管轄判定

def jurisdiction(repo, filepath):
    """
    (管轄内か, 理由) を返す。
    管轄内: repo 配下にあり、曲フォルダ (repo root ではない、直下に .sib が
    あるフォルダ) の直下にある .sib で、予約名フォルダ配下ではない。
    """
    if repo is None:
        return (False, "no repo configured")
    ap = os.path.abspath(filepath)
    if not ap.lower().endswith(".sib"):
        return (False, "not a .sib file")
    try:
        rel = os.path.relpath(ap, repo)
    except ValueError:
        return (False, "outside repo")
    if rel.startswith(".." + os.sep) or rel == "..":
        return (False, "outside repo")
    parts = rel.split(os.sep)
    if len(parts) < 2:
        return (False, "file is at repo root, not in a song folder")
    for part in parts[:-1]:
        if part.startswith(RESERVED_PREFIXES):
            return (False, "under a reserved folder (%s)" % part)
    return (True, "in song folder %s" % os.path.dirname(rel))


# ---------------------------------------------------------------- save

def cmd_save(kp, filepath):
    repo = kp.repo
    if repo is None:
        print("NOT_CONFIGURED: run 'keeper.py config --repo <path>' first")
        return 3
    inside, reason = jurisdiction(repo, filepath)
    if not inside:
        print("OUT_OF_JURISDICTION: %s" % reason)
        return 2

    ap = os.path.abspath(filepath)
    song_dir = os.path.dirname(ap)
    song_name = os.path.basename(song_dir)
    rel_file = os.path.relpath(ap, repo)
    gitignore_path = os.path.join(song_dir, ".gitignore")
    rel_ignore = os.path.relpath(gitignore_path, repo)

    with RepoLock(repo):
        # 登録: .gitignore が無い場合のみ既定値で作成する。既存には触れない
        created_ignore = False
        if not os.path.exists(gitignore_path):
            with open(gitignore_path, "w", encoding="utf-8") as f:
                f.write(DEFAULT_GITIGNORE)
            created_ignore = True

        targets = [rel_file] + ([rel_ignore] if created_ignore else [])
        rc, out, err = run_git(repo, ["status", "--porcelain", "--"] + targets)
        if rc != 0:
            kp.state["last_error"] = "git status failed: %s" % err.strip()
            kp.save_state()
            print("ERROR: git status failed: %s" % err.strip())
            return 1

        committed = False
        if out.strip():
            rc, _o, err = run_git(repo, ["add", "--"] + targets)
            if rc != 0:
                kp.state["last_error"] = "git add failed: %s" % err.strip()
                kp.save_state()
                print("ERROR: git add failed: %s" % err.strip())
                return 1
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            msg = "save: %s %s" % (song_name, stamp)
            rc, _o, err = run_git(repo, ["commit", "-m", msg, "--"] + targets)
            if rc != 0:
                kp.state["last_error"] = "git commit failed: %s" % err.strip()
                kp.save_state()
                print("ERROR: git commit failed: %s" % err.strip())
                return 1
            committed = True
            print("COMMITTED: %s" % msg)
        else:
            print("NO_CHANGE: %s" % rel_file)

        # 未送信があれば push を試みる (変更なし保存でも再送する)
        pending = unpushed_count(repo)
        if pending == 0:
            return 0
        return do_push(kp, repo, pending, committed)


def do_push(kp, repo, pending, committed):
    rc_b, branch, _ = run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch.strip() if rc_b == 0 else "HEAD"
    rc_u, _o, _e = run_git(repo, ["rev-parse", "--abbrev-ref", "@{upstream}"])
    if rc_u == 0:
        push_args = ["push"]
    else:
        push_args = ["push", "-u", "origin", branch]
    rc, _out, err = run_git(repo, push_args)
    if rc == 0:
        kp.state["last_push"] = now_iso()
        kp.state.pop("last_error", None)
        kp.state.pop("conflict", None)
        kp.save_state()
        print("PUSHED: %d commit(s)" % pending)
        return 0

    err_text = err.strip()
    lowered = err_text.lower()
    if ("non-fast-forward" in lowered or "[rejected]" in lowered
            or "fetch first" in lowered or "tip of your current branch is behind" in lowered):
        # 競合: force push は絶対にしない。ローカル commit を保全して記録する
        kp.state["conflict"] = {
            "at": now_iso(),
            "branch": branch,
            "unpushed": pending,
            "detail": err_text[-500:],
        }
        kp.state["last_error"] = "conflict: remote is newer; local commits kept, not pushed"
        kp.save_state()
        print("CONFLICT: remote is newer. Local commit(s) kept; nothing was force-pushed.")
        return 4

    kp.state["last_error"] = "push failed: %s" % err_text[-500:]
    kp.save_state()
    print("PUSH_FAILED: local commit(s) kept (%d unpushed). %s"
          % (pending, err_text[-200:]))
    return 5


# ---------------------------------------------------------------- status

def repo_visibility(repo):
    """分かる範囲で可視性を返す。gh が使えなければ 'unknown'。"""
    rc, remote, _ = run_git(repo, ["remote", "get-url", "origin"])
    remote = remote.strip()
    if rc != 0 or "github.com" not in remote:
        return ("unknown", remote)
    try:
        p = subprocess.run(
            ["gh", "repo", "view", remote, "--json", "visibility",
             "-q", ".visibility"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        if p.returncode == 0:
            vis = p.stdout.decode("utf-8", "replace").strip().lower()
            if vis:
                return (vis, remote)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ("unknown", remote)


def build_status(kp, filepath):
    repo = kp.repo
    lines = []
    if repo is None:
        lines.append("状態: 未設定 (対象 repo が設定されていません)")
        return ("\n".join(lines), 3)
    inside, reason = jurisdiction(repo, filepath) if filepath else (None, "")
    lines.append("repo: %s" % repo)
    if filepath:
        lines.append("ファイル: %s" % os.path.abspath(filepath))
        lines.append("管轄: %s (%s)" % ("内" if inside else "外", reason))
    vis, remote = repo_visibility(repo)
    lines.append("remote: %s" % (remote or "(なし)"))
    lines.append("可視性: %s" % vis)
    sc_state, _ev = shortcut_state(kp)
    lines.append("Cmd+S: %s" % SHORTCUT_STATE_LABELS[sc_state])
    lines.append("未送信 commit: %d 件" % unpushed_count(repo))
    lines.append("repo サイズ: %s" % fmt_size(repo_size_bytes(repo)))
    lines.append("最終 push: %s" % kp.state.get("last_push", "(記録なし)"))
    if kp.state.get("conflict"):
        c = kp.state["conflict"]
        lines.append("競合: あり (%s 時点, 未送信 %s 件) - remote の方が新しい版があります"
                     % (c.get("at", "?"), c.get("unpushed", "?")))
    if kp.state.get("last_error"):
        lines.append("最後のエラー: %s" % kp.state["last_error"])
    return ("\n".join(lines), 0)


def cmd_status(kp, filepath, open_result):
    text, code = build_status(kp, filepath)
    print(text)
    if open_result:
        out_path = os.path.join(kp.config_dir, "last-status.txt")
        os.makedirs(kp.config_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        # macOS では既定のテキストエディタで開く (他 OS では場所だけ表示)
        if sys.platform == "darwin":
            subprocess.Popen(["open", out_path])
        else:
            print("status written to: %s" % out_path)
    return code


# ---------------------------------------------------------------- Cmd+S 割り当て状態
#
# ManuScript にはショートカットの読み取り・変更 API が無いため、割り当て
# 状態は外側の痕跡から間接的に判定する (読み取り専用。書き換えはしない)。
#
#   confirmed  (確認済み)  : cmds_confirmed.flag がある
#                            (起動時ダイアログを一度表示し、利用者が承知した)
#   unassigned (未割り当て): カスタム .sfs が無く、plist の CommandSet が
#                            標準セットのまま。この2点が揃えば「Cmd+S は
#                            まだ『gitと併せて保存』ではない」と確定できる
#   unknown    (要確認)    : カスタム .sfs がある、または CommandSet が標準
#                            でない。何らかのカスタマイズはあるが、それが
#                            Cmd+S の割り当てかどうかまでは痕跡だけでは
#                            判定できない (.sfs の差分解析は将来の課題)
#
# 判定結果は cmds_state (人が読む用) と cmds_unassigned.flag (プラグインの
# Initialize が FileExists で読む用) に維持する。更新は watcher と
# check-shortcut が行う。plist はファイル直読み (plistlib) とする。
# 実機確認 (2026-11-15): defaults read とファイル直読みは同値で、
# cfprefsd は外部からのファイル変更を即時に反映する。ただし Sibelius
# 自身は起動中 plist を書き戻さない (現行セッション開始前の mtime のまま)
# ため、セッション中の変更は終了時の書き戻しまでは見えないことがある。
# マーカーの消費は次回起動時なので、その時点で鮮度は間に合う。

STANDARD_COMMAND_SET = "標準メニューとショートカット"
SFS_PRUNE_DIRS = {"QtWebEngine", "PlogueEngine", "Logs", "ScoreThumbnails",
                  "ManuscriptPaperThumbnails", "自動保存"}


def sibelius_user_dir():
    return os.path.join(os.path.expanduser("~"), "Library",
                        "Application Support", "Avid", "Sibelius")


def sibelius_plist_path():
    return os.path.join(os.path.expanduser("~"), "Library", "Preferences",
                        "com.avid.sibelius.plist")


def find_custom_sfs():
    """Sibelius ユーザーデータ領域にあるカスタム .sfs の一覧を返す。"""
    base = sibelius_user_dir()
    found = []
    if not os.path.isdir(base):
        return found
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SFS_PRUNE_DIRS]
        for name in filenames:
            if name.lower().endswith(".sfs"):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def read_command_set_name():
    """plist から現在のショートカットセット名を読む。読めなければ None。"""
    path = sibelius_plist_path()
    try:
        with open(path, "rb") as f:
            data = plistlib.load(f)
        node = data.get("Preferences", {}).get("Preferences", {})
        command_set = node.get("CommandSet", {})
        name = command_set.get("Data")
        return name if isinstance(name, str) else None
    except (OSError, ValueError, AttributeError):
        return None


def shortcut_state(kp):
    """(状態, 根拠 dict) を返す。状態は confirmed / unassigned / unknown。"""
    evidence = {}
    try:
        confirmed_path = os.path.join(kp.config_dir, "cmds_confirmed.flag")
        evidence["cmds_confirmed"] = os.path.exists(confirmed_path)
        sfs = find_custom_sfs()
        evidence["custom_sfs"] = sfs
        evidence["command_set"] = read_command_set_name()
        if evidence["cmds_confirmed"]:
            return ("confirmed", evidence)
        if not sfs and evidence["command_set"] == STANDARD_COMMAND_SET:
            return ("unassigned", evidence)
        return ("unknown", evidence)
    except Exception as e:  # 判定の失敗で本体処理を止めない
        evidence["error"] = str(e)
        return ("unknown", evidence)


SHORTCUT_STATE_LABELS = {
    "confirmed": "確認済み",
    "unassigned": "未割り当て",
    "unknown": "要確認",
}


def update_shortcut_marker(kp):
    """判定し、cmds_state と cmds_unassigned.flag を実状態へ寄せる。"""
    state, evidence = shortcut_state(kp)
    os.makedirs(kp.config_dir, exist_ok=True)
    lines = [
        "state: %s" % state,
        "checked_at: %s" % now_iso(),
        "cmds_confirmed: %s" % bool(evidence.get("cmds_confirmed")),
        "command_set: %s" % evidence.get("command_set"),
        "custom_sfs_count: %d" % len(evidence.get("custom_sfs") or []),
    ]
    for path in evidence.get("custom_sfs") or []:
        lines.append("custom_sfs: %s" % path)
    if evidence.get("error"):
        lines.append("error: %s" % evidence["error"])
    marker_path = os.path.join(kp.config_dir, "cmds_state")
    tmp = marker_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, marker_path)
    flag_path = os.path.join(kp.config_dir, "cmds_unassigned.flag")
    if state == "unassigned":
        with open(flag_path, "w", encoding="utf-8") as f:
            f.write(now_iso() + "\n")
    else:
        try:
            os.unlink(flag_path)
        except OSError:
            pass
    return state, evidence


def cmd_check_shortcut(kp):
    state, evidence = update_shortcut_marker(kp)
    print("Cmd+S: %s (%s)" % (SHORTCUT_STATE_LABELS[state], state))
    print("  セット: %s" % evidence.get("command_set"))
    print("  カスタム .sfs: %d 件" % len(evidence.get("custom_sfs") or []))
    print("  確認済みフラグ: %s" % bool(evidence.get("cmds_confirmed")))
    print("  マーカー: %s" % os.path.join(kp.config_dir, "cmds_state"))
    return 0


# ---------------------------------------------------------------- config / flag

def cmd_config(kp, repo_path, show):
    if show:
        print(json.dumps(kp.config, ensure_ascii=False, indent=2))
        return 0
    if not repo_path:
        print("ERROR: --repo or --show is required")
        return 1
    ap = os.path.abspath(os.path.expanduser(repo_path))
    if not os.path.isdir(ap):
        print("ERROR: not a directory: %s" % ap)
        return 1
    rc, top, _ = run_git(ap, ["rev-parse", "--show-toplevel"])
    if rc != 0:
        print("ERROR: not a git repository: %s" % ap)
        return 1
    root = top.strip()
    kp.config["repo"] = root
    kp.config["configured_at"] = now_iso()
    kp._save_json(kp.config_path, kp.config)
    print("CONFIGURED: repo = %s" % root)
    print("config file: %s" % kp.config_path)
    return 0


FLAG_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def cmd_flag(kp, name, on, off):
    # フラグ名はファイル名に直接使うため、パス区切りや空白などを含む
    # 名前は受け付けない (プラグインからの呼び出しは固定名のみ)
    if not name or not FLAG_NAME_RE.match(name):
        print("ERROR: 不正なフラグ名です: %r (使える文字は英数字・ハイフン・アンダースコア)" % name)
        return 1
    flag_path = os.path.join(kp.config_dir, "%s.flag" % name)
    if on:
        os.makedirs(kp.config_dir, exist_ok=True)
        with open(flag_path, "w", encoding="utf-8") as f:
            f.write(now_iso() + "\n")
        print("FLAG ON: %s" % name)
        return 0
    if off:
        try:
            os.unlink(flag_path)
        except OSError:
            pass
        print("FLAG OFF: %s" % name)
        return 0
    print("FLAG %s: %s" % (name, "ON" if os.path.exists(flag_path) else "OFF"))
    return 0


# ---------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(prog="keeper.py")
    parser.add_argument("--config", default=None,
                        help="設定ファイルのパス (既定: ~/.config/sibelius-git-keeper/config.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_save = sub.add_parser("save")
    p_save.add_argument("--file", required=True)

    p_status = sub.add_parser("status")
    p_status.add_argument("--file", default=None)
    p_status.add_argument("--open", action="store_true",
                          help="結果をファイルに書いて開く (プラグイン用)")

    p_config = sub.add_parser("config")
    p_config.add_argument("--repo", default=None)
    p_config.add_argument("--show", action="store_true")

    p_flag = sub.add_parser("flag")
    p_flag.add_argument("--name", required=True)
    p_flag.add_argument("--on", action="store_true")
    p_flag.add_argument("--off", action="store_true")

    sub.add_parser("check-shortcut")

    args = parser.parse_args(argv)
    kp = Keeper(args.config or default_config_path())

    if args.command == "save":
        return cmd_save(kp, args.file)
    if args.command == "status":
        return cmd_status(kp, args.file, args.open)
    if args.command == "config":
        return cmd_config(kp, args.repo, args.show)
    if args.command == "flag":
        return cmd_flag(kp, args.name, args.on, args.off)
    if args.command == "check-shortcut":
        return cmd_check_shortcut(kp)
    return 1


if __name__ == "__main__":
    sys.exit(main())
