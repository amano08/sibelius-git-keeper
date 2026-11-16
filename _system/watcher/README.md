# 監視（watcher）

プラグインを経由しない保存（ツールバーの保存ボタンなど）を取りこぼさないための保険です。

## 実装方針（試作）

- 仕様では FSEvents を想定していますが、試作では外部依存（fswatch 等）を避けるため、**Python のポーリング監視**で実装しています（`watcher.py`）
- 対象 repo は設定ファイル（`~/.config/sibelius-git-keeper/config.json`）から読みます
- 監視対象は、管轄内になり得る .sib 本体のみです（予約フォルダ配下、バックアップ類は除く）
- 変更を検知したら、書き込みが落ち着くまで待って（既定 8 秒）から `keeper.py save` を呼びます。git 操作とロックは keeper 側に集約されています
- pull は行いません。譜面ファイルを書き換えることもありません

## 起動

- 通常は setup.sh が LaunchAgent（`com.sibelius-git-keeper.watcher.plist`）を登録し、ログイン時に自動で起動します
- 手動で試す場合: `python3 watcher.py [--interval 秒] [--quiet 秒]`

## ログ

LaunchAgent 経由の場合、`~/Library/Logs/sibelius-git-keeper/watcher.log` に出ます。
