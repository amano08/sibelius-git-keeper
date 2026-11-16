# ManuScript プラグイン

このフォルダのプラグインは、**Sibelius 25.4（Mac）で実機検証済み** です。他のバージョンや Windows では未検証です。

## 構成

ManuScript では 1 つの .plg が 1 つのメニューコマンドに対応するため、コマンドごとにファイルを分けています。

| ファイル | コマンド | 働き |
| --- | --- | --- |
| GitKeeperSave.plg | gitと併せて保存 | 保存し、管轄内なら keeper.py save を背面起動。管轄外なら通常保存のみ＋管轄外メッセージ表示（抑制フラグ対応）。Cmd+S の割り当て先 |
| GitKeeperImport.plg | 管轄内へ取り込む | 管轄内なら登録を実行。管轄外なら Save As での保存し直しを案内（下記の制限参照） |
| GitKeeperStatus.plg | 状態確認 | keeper.py status --open を起動し、結果ファイルを開く |
| GitKeeperSettings.plg | 設定 | 対象 repo の表示と、管轄外メッセージ抑制の解除（再表示） |

- ソースは `src/*.plg.txt`（UTF-8）。`generate_plg.py` で UTF-16BE + BOM の `.plg` を生成します
- ソース内の `__REPO_ROOT__` と `__HOME__` は、setup.sh が配置時に実際の値へ置換します。template に入っている生成済み .plg はプレースホルダのままです

## 管轄外メッセージについて

文案は仕様書第6章の指定どおり、一字一句そのまま GitKeeperSave.plg に入っています。表示後に抑制フラグ（`~/.config/sibelius-git-keeper/suppress_ooj.flag`）を立て、以降は表示しません。再表示は GitKeeperSettings.plg（設定）から行えます。

## 制限

- **取り込みにフォルダ選択 UI は使っていません**。フォルダ選択 API（`Sibelius.SelectFolder`）自体は実在を確認済みですが、選択結果のパス形式が未検証のため、v1 では「名前を付けて保存で曲フォルダへ保存し直してから再実行」という案内方式にしています
- 状態確認は、外部コマンドの出力をプラグイン内ダイアログで表示するのではなく、keeper が書いたテキストファイルを開く方式です

## 実機検証で確認済みの事項（Sibelius 25.4 / Mac）

- .plg の構造と符号化（UTF-16BE + BOM）で、4コマンドともプラグインとして読み込まれ、実行できること
- プラグイン名は拡張子なしのファイル名で決まること
- `Sibelius.LaunchApplication` の引数は `CreateSparseArray(...)` 形式が必須であること（空白区切りの1文字列では起動しない）
- 文字列関数は `Substring(s, start, len)` であること（`Substr` は存在しない）。`CharAt`、`Length` は使用可
- `Sibelius.FileExists`、`Sibelius.ActiveScore`、`Score.FileName`（絶対パス）が使用できること
- 保存は組み込みの保存コマンド（`Sibelius.Execute`）を使うこと。未編集でも保存のたびに .sib 全体が別バイトで書き直されるため、「gitと併せて保存」は実行の都度 commit が作られる
