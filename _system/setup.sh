#!/usr/bin/env bash
# sibelius-git-keeper セットアップコマンド
#
# 使い方: clone した repo の中で _system/setup.sh を実行する。
# 何度実行しても安全 (冪等)。再実行は更新を兼ねる。
# 差し替えるのは _system とプラグインだけ。利用者の .gitignore、
# 設定のうち利用者が変えた項目、曲フォルダには触れない。
#
# オプション:
#   --dry-run   実際の変更を行わず、行う内容だけを表示する
#
# テスト用に宛先を差し替える環境変数 (通常は不要):
#   KEEPER_PLUGIN_DIR       プラグイン配置先
#   KEEPER_CONFIG_DIR       設定ディレクトリ
#   KEEPER_LAUNCHAGENT_DIR  LaunchAgent 配置先
#   KEEPER_YES=1            確認プロンプトに自動で yes と答える
set -u

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then DRY_RUN=1; fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLUGIN_DIR="${KEEPER_PLUGIN_DIR:-$HOME/Library/Application Support/Avid/Sibelius/Plugins}"
CONFIG_DIR="${KEEPER_CONFIG_DIR:-$HOME/.config/sibelius-git-keeper}"
CONFIG_FILE="$CONFIG_DIR/config.json"
LA_DIR="${KEEPER_LAUNCHAGENT_DIR:-$HOME/Library/LaunchAgents}"
LA_PLIST="$LA_DIR/com.sibelius-git-keeper.watcher.plist"
KEEPER_PY="$SCRIPT_DIR/keeper.py"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [OK] %s\n' "$*"; }
warn() { printf '  [!] %s\n' "$*"; }
stop_with() { printf '\n停止: %s\n' "$*"; exit 1; }

confirm() {
    # $1 = 説明文。変更を伴う操作の前に必ず内容を表示して確認する
    say ""
    say "これから行う変更: $1"
    if [ "$DRY_RUN" = "1" ]; then say "(dry-run のため実行しません)"; return 1; fi
    if [ "${KEEPER_YES:-}" = "1" ]; then say "(KEEPER_YES=1 のため自動承認)"; return 0; fi
    printf '実行してよいですか? [y/N] '
    read -r ans
    case "$ans" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

say "=== sibelius-git-keeper セットアップ ==="
say "対象 repo: $REPO_ROOT"
[ "$DRY_RUN" = "1" ] && say "(dry-run モード)"

# --- 1. git / gh の有無 -------------------------------------------------
HAS_GH=0
command -v git >/dev/null 2>&1 || stop_with "git が見つかりません。Xcode Command Line Tools (xcode-select --install) などで導入してください"
ok "git: $(git --version)"
if command -v gh >/dev/null 2>&1; then
    HAS_GH=1
    ok "gh: $(gh --version | head -1)"
else
    ok "gh: 未導入 (任意) — gh なし経路で進めます。導入は不要です"
fi
command -v python3 >/dev/null 2>&1 || stop_with "python3 が見つかりません。ヘルパーの実行に必要です"

# --- 2. Sibelius 起動中チェック ------------------------------------------
if pgrep -f "Sibelius" >/dev/null 2>&1; then
    stop_with "Sibelius が起動しています。終了してから再実行してください (プラグイン配置と読み込みのため)"
fi
ok "Sibelius は起動していません"

# --- 3. GitHub 認証と git identity ----------------------------------------
if [ "$HAS_GH" = "1" ]; then
    AUTH_MODE="gh"
    if gh auth status >/dev/null 2>&1; then
        LOGIN="$(gh api user --jq .login 2>/dev/null || echo '(取得できません)')"
        ok "gh ログイン中: $LOGIN"
    else
        stop_with "gh にログインしていません。gh auth login を実行してから再実行してください"
    fi
else
    AUTH_MODE="ssh"
    SSH_AUTH_OUT="$(ssh -T -o BatchMode=yes -o ConnectTimeout=10 git@github.com 2>&1 || true)"
    if printf '%s' "$SSH_AUTH_OUT" | grep -q "successfully authenticated"; then
        LOGIN="$(printf '%s' "$SSH_AUTH_OUT" | sed -n 's/.*Hi \([^!]*\)!.*/\1/p' | head -1)"
        [ -n "$LOGIN" ] || LOGIN="(アカウント名を取得できません)"
        ok "GitHub SSH 認証: $LOGIN"
    else
        stop_with "GitHub への SSH 認証を確認できません。GitHub に登録した SSH 鍵で ssh -T git@github.com が成功する状態にしてから再実行してください"
    fi
fi

GIT_NAME="$(git config --global user.name || true)"
GIT_EMAIL="$(git config --global user.email || true)"
if [ -n "$GIT_NAME" ] && [ -n "$GIT_EMAIL" ]; then
    ok "git identity: $GIT_NAME <$GIT_EMAIL>"
else
    stop_with "git の user.name / user.email が未設定です。git config --global user.name \"名前\" と user.email を設定してから再実行してください"
fi

# --- 4. repo であること、remote、可視性ゲート -------------------------------
git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1 || stop_with "$REPO_ROOT は git repo ではありません。clone したフォルダで実行してください"
REMOTE_URL="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)"
if [ -z "$REMOTE_URL" ]; then
    stop_with "remote 'origin' が未設定です。repo の可視性を確認できないため、推測で進めず停止します。GitHub に repo を作成し、remote を追加してから再実行してください"
fi
ok "remote origin: $REMOTE_URL"

# GitHub の owner/repo を取り出す (可視性ゲートと API 確認に使う)
case "$REMOTE_URL" in
  git@github.com:*) REPO_SLUG="${REMOTE_URL#git@github.com:}" ;;
  ssh://git@github.com/*) REPO_SLUG="${REMOTE_URL#ssh://git@github.com/}" ;;
  https://github.com/*) REPO_SLUG="${REMOTE_URL#https://github.com/}" ;;
  http://github.com/*) REPO_SLUG="${REMOTE_URL#http://github.com/}" ;;
  *) stop_with "remote 'origin' が GitHub の repo を指していません: $REMOTE_URL" ;;
esac
REPO_SLUG="${REPO_SLUG%%#*}"
REPO_SLUG="${REPO_SLUG%%\?*}"
REPO_SLUG="${REPO_SLUG%/}"
REPO_SLUG="${REPO_SLUG%.git}"
case "$REPO_SLUG" in
  */*/*|/*|*/|""|.*/*|*/.*|*" "*) stop_with "remote 'origin' から owner/repo を特定できません: $REMOTE_URL" ;;
  */*) : ;;
  *) stop_with "remote 'origin' から owner/repo を特定できません: $REMOTE_URL" ;;
esac
REPO_OWNER="${REPO_SLUG%%/*}"
REPO_NAME="${REPO_SLUG#*/}"
ok "対象 GitHub repo: $REPO_SLUG"

# HTTPS の GitHub remote は SSH 形式へ寄せる (仕様 7.2-6)
case "$REMOTE_URL" in
  https://github.com/*)
    SSH_URL="git@github.com:$(printf '%s' "$REMOTE_URL" | sed -e 's|^https://github.com/||')"
    case "$SSH_URL" in *.git) : ;; *) SSH_URL="${SSH_URL}.git" ;; esac
    if confirm "remote の URL を HTTPS から SSH へ変更します: $REMOTE_URL -> $SSH_URL"; then
        git -C "$REPO_ROOT" remote set-url origin "$SSH_URL" && ok "remote を SSH 形式にしました"
        REMOTE_URL="$SSH_URL"
    else
        warn "remote の変更を見送りました"
    fi
    ;;
esac

say ""
say "repo の可視性を確認します (認証経路: $AUTH_MODE)"
if [ "$HAS_GH" = "1" ]; then
    VISIBILITY="$(gh repo view "$REPO_SLUG" --json visibility -q .visibility 2>/dev/null || echo '')"
    case "$VISIBILITY" in
      PUBLIC)
        stop_with "この repo は PUBLIC です。本システムは private repo 専用です。push に関わる処理は一切行いません。Private へ変更するか、作り直してから再実行してください"
        ;;
      PRIVATE)
        ok "repo の可視性: PRIVATE (gh で確認)"
        ;;
      *)
        stop_with "gh で repo の可視性を判定できませんでした。推測で進めず停止します (visibility='${VISIBILITY:-取得失敗}')"
        ;;
    esac
    PERM="$(gh repo view "$REPO_SLUG" --json viewerPermission -q .viewerPermission 2>/dev/null || echo '')"
    case "$PERM" in
      ADMIN|MAINTAIN|WRITE) ok "push 権限: $PERM" ;;
      *) stop_with "この repo への push 権限を確認できませんでした (viewerPermission='${PERM:-取得失敗}')" ;;
    esac
else
    # gh なし経路: SSH で到達できる repo が、未認証 API からは見えない
    # (= 404) ことをもって private と判定する。どちらか一方でも欠けたり、
    # API が 200 / その他の応答なら推測で進めず停止する。
    if GIT_TERMINAL_PROMPT=0 git ls-remote "$REMOTE_URL" HEAD >/dev/null 2>&1; then
        ok "git ls-remote: 成功 (repo が存在し、SSH でアクセスできます)"
    else
        stop_with "git ls-remote に失敗しました。repo の存在または SSH アクセスを確認できないため停止します"
    fi
    command -v curl >/dev/null 2>&1 || stop_with "curl が見つかりません。gh なし経路では可視性を確認できないため停止します"
    API_TMP="$(mktemp)"
    HTTP_CODE="$(curl --silent --show-error --netrc-file /dev/null \
        -H 'Accept: application/vnd.github+json' \
        -H 'X-GitHub-Api-Version: 2022-11-28' \
        -H 'Authorization:' \
        -o "$API_TMP" -w '%{http_code}' \
        "https://api.github.com/repos/$REPO_SLUG" 2>/dev/null || true)"
    rm -f "$API_TMP"
    [ -n "$HTTP_CODE" ] || HTTP_CODE="000"
    case "$HTTP_CODE" in
      404)
        ok "repo の可視性: PRIVATE (SSH では到達可能、未認証 API では 404)"
        ;;
      200)
        stop_with "この repo は PUBLIC です (未認証 API で確認できました)。本システムは private repo 専用です。push に関わる処理は一切行いません。Private へ変更するか、作り直してから再実行してください"
        ;;
      *)
        stop_with "未認証 API の応答が HTTP $HTTP_CODE のため、repo の可視性を判定できません。推測で進めず停止します"
        ;;
    esac
    if [ "$LOGIN" = "$REPO_OWNER" ]; then
        ok "SSH アカウントは repo の owner と一致します"
    else
        warn "SSH アカウント ($LOGIN) と repo owner ($REPO_OWNER) が一致しません。push 可否は最後の push ドライランで確認します"
    fi
fi

# --- 5. SSH 疎通 -------------------------------------------------------------
if [ "$HAS_GH" = "1" ]; then
    SSH_OUT="$(ssh -T -o BatchMode=yes -o ConnectTimeout=10 git@github.com 2>&1 || true)"
    case "$SSH_OUT" in
      *"successfully authenticated"*) ok "GitHub への SSH 疎通: OK" ;;
      *) warn "GitHub への SSH 疎通を確認できませんでした: $SSH_OUT" ;;
    esac
else
    ok "GitHub への SSH 疎通: 認証と git ls-remote で確認済み"
fi

# --- 6. プラグイン配置 --------------------------------------------------------
say ""
say "プラグイン配置先: $PLUGIN_DIR"
if [ "$DRY_RUN" = "1" ]; then
    say "(dry-run) $SCRIPT_DIR/plugin/src/*.plg.txt から __REPO_ROOT__ を $REPO_ROOT に置換して生成し、配置します"
else
    mkdir -p "$PLUGIN_DIR"
    GENERATED=0
    for src in "$SCRIPT_DIR"/plugin/src/*.plg.txt; do
        [ -e "$src" ] || continue
        base="$(basename "$src" .plg.txt)"
        python3 - "$src" "$PLUGIN_DIR/$base.plg" "$REPO_ROOT" "$HOME" <<'PYEOF'
import sys
src, dst, repo, home = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(src, "r", encoding="utf-8") as f:
    text = f.read()
text = text.replace("__REPO_ROOT__", repo).replace("__HOME__", home)
with open(dst, "wb") as f:
    f.write(b"\xfe\xff" + text.encode("utf-16-be"))
print("installed plugin: %s" % dst)
PYEOF
        GENERATED=1
    done
    if [ "$GENERATED" = "0" ]; then
        for plg in "$SCRIPT_DIR"/plugin/*.plg; do
            [ -e "$plg" ] || continue
            cp "$plg" "$PLUGIN_DIR/" && say "installed plugin: $PLUGIN_DIR/$(basename "$plg")"
        done
    fi
    ok "プラグインを配置しました"
fi

# keeper 起動用ランチャー (プラグインが keeper.py を呼ぶための固定パス)
LAUNCHER="$CONFIG_DIR/keeper-launch.sh"
if [ "$DRY_RUN" = "1" ]; then
    say "(dry-run) ランチャーを $LAUNCHER に書きます"
else
    mkdir -p "$CONFIG_DIR"
    cat > "$LAUNCHER" <<EOF
#!/bin/sh
# setup.sh が生成。keeper.py の場所をプラグインへ渡すためのランチャー。
exec /usr/bin/env python3 "$KEEPER_PY" "\$@"
EOF
    chmod +x "$LAUNCHER"
    ok "ランチャーを書きました: $LAUNCHER"
fi

# --- 7. 対象 repo の登録 (設定ファイル) ---------------------------------------
if [ "$DRY_RUN" = "1" ]; then
    say "(dry-run) keeper.py config --repo $REPO_ROOT を実行します"
else
    KEEPER_CONFIG_ARG=""
    if [ "$CONFIG_DIR" != "$HOME/.config/sibelius-git-keeper" ]; then
        KEEPER_CONFIG_ARG="--config $CONFIG_FILE"
    fi
    # shellcheck disable=SC2086
    python3 "$KEEPER_PY" $KEEPER_CONFIG_ARG config --repo "$REPO_ROOT" || stop_with "repo の登録に失敗しました"
    ok "対象 repo を設定に記録しました"
fi

# --- 8. 監視 (LaunchAgent) ------------------------------------------------------
PLIST_SRC="$SCRIPT_DIR/watcher/com.sibelius-git-keeper.watcher.plist"
if [ "$(uname)" = "Darwin" ] && [ -f "$PLIST_SRC" ]; then
    if [ "$DRY_RUN" = "1" ]; then
        say "(dry-run) LaunchAgent を $LA_PLIST に配置し、読み込みます"
    else
        mkdir -p "$LA_DIR" "$HOME/Library/Logs/sibelius-git-keeper"
        sed -e "s|__WATCHER_PATH__|$SCRIPT_DIR/watcher/watcher.py|g" \
            -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
            -e "s|__HOME__|$HOME|g" \
            "$PLIST_SRC" > "$LA_PLIST"
        launchctl unload "$LA_PLIST" >/dev/null 2>&1 || true
        if launchctl load "$LA_PLIST" 2>/dev/null; then
            ok "監視 (LaunchAgent) を登録しました"
        else
            warn "LaunchAgent の読み込みに失敗しました。手動で確認してください: $LA_PLIST"
        fi
    fi
else
    say "(この環境では LaunchAgent 登録をスキップします。plist の雛形: $PLIST_SRC)"
fi

# --- 9. push ドライラン ---------------------------------------------------------
if [ -n "$REMOTE_URL" ]; then
    if git -C "$REPO_ROOT" push --dry-run origin >/dev/null 2>&1; then
        ok "push ドライラン: OK"
    else
        warn "push ドライランに失敗しました。remote の状態 (未 push の初回など) によっては正常な場合もあります"
    fi
else
    warn "remote 未設定のため push ドライランをスキップしました"
fi

say ""
say "=== セットアップ完了 ==="
say "残りの手作業:"
say "  1. Sibelius を起動する (プラグイン読み込みのため)"
say "  2. 設定画面で「gitと併せて保存」に Cmd+S を割り当てる (README の「Cmd+S の割り当て」参照)"
say "  3. my_songs/sample_song のサンプル譜面で試し運転する"
