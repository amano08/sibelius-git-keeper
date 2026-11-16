#!/usr/bin/env bash
# sibelius-git-keeper アンインストール
#
# 行うこと:
#   - プラグインをプラグインフォルダから取り除く
#   - 監視の LaunchAgent を解除する
#   - Cmd+S の割り当てを戻す手順を案内する (自動では戻さない)
#
# 行わないこと:
#   - repo、commit の履歴、譜面ファイルへの変更は一切しない
#   - 設定ファイルは残す (場所を表示する。消したい場合は手動で削除)
#
# 環境変数: setup.sh と同じ KEEPER_* 上書きが使える。KEEPER_YES=1 で確認を省略。
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="${KEEPER_PLUGIN_DIR:-$HOME/Library/Application Support/Avid/Sibelius/Plugins}"
CONFIG_DIR="${KEEPER_CONFIG_DIR:-$HOME/.config/sibelius-git-keeper}"
LA_DIR="${KEEPER_LAUNCHAGENT_DIR:-$HOME/Library/LaunchAgents}"
LA_PLIST="$LA_DIR/com.sibelius-git-keeper.watcher.plist"

say() { printf '%s\n' "$*"; }

say "=== sibelius-git-keeper アンインストール ==="
say "対象:"
say "  プラグイン配置先: $PLUGIN_DIR"
say "  LaunchAgent:      $LA_PLIST"
say "repo、譜面、commit 履歴には一切触れません。"
if [ "${KEEPER_YES:-}" != "1" ]; then
    printf '続けてよいですか? [y/N] '
    read -r ans
    case "$ans" in y|Y|yes|YES) : ;; *) say "中止しました"; exit 0 ;; esac
fi

# LaunchAgent 解除
if [ -f "$LA_PLIST" ]; then
    launchctl unload "$LA_PLIST" >/dev/null 2>&1 || true
    rm -f "$LA_PLIST"
    say "監視 (LaunchAgent) を解除しました"
else
    say "LaunchAgent は登録されていませんでした"
fi

# プラグイン除去 (本システムの .plg のみ)
REMOVED=0
for src in "$SCRIPT_DIR"/plugin/src/*.plg.txt; do
    [ -e "$src" ] || continue
    base="$(basename "$src" .plg.txt)"
    if [ -f "$PLUGIN_DIR/$base.plg" ]; then
        rm -f "$PLUGIN_DIR/$base.plg"
        say "プラグインを取り除きました: $base.plg"
        REMOVED=1
    fi
done
for plg in "$SCRIPT_DIR"/plugin/*.plg; do
    [ -e "$plg" ] || continue
    name="$(basename "$plg")"
    if [ -f "$PLUGIN_DIR/$name" ]; then
        rm -f "$PLUGIN_DIR/$name"
        say "プラグインを取り除きました: $name"
        REMOVED=1
    fi
done
[ "$REMOVED" = "0" ] && say "取り除くプラグインはありませんでした"

say ""
say "残りの手作業:"
say "  - Sibelius の設定画面で、Cmd+S の割り当てを通常の保存へ戻してください"
say "    (割り当てはあなたの設定のため、自動では戻しません)"
say "任意で消せるもの:"
say "  - 設定ファイル: $CONFIG_DIR (状態ファイルやランチャーを含みます)"
say "GitHub 上の repo と版の履歴はそのまま残ります。"
