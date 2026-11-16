# ManuScript プラグイン（試作初稿・実機未検証）

このフォルダのプラグインは **実機（Sibelius 本体）での検証が未完了の初稿** です。読み込めない、動作しない可能性があります。検証は次フェーズ（Studio 実機）で行います。

## 構成

ManuScript では 1 つの .plg が 1 つのメニューコマンドに対応するため、コマンドごとにファイルを分けています。

| ファイル | コマンド | 働き |
| --- | --- | --- |
| GitKeeperSave.plg | gitと併せて保存 | 保存し、管轄内なら keeper.py save を背面起動。管轄外なら通常保存のみ＋管轄外メッセージ表示（抑制フラグ対応）。Cmd+S の割り当て先 |
| GitKeeperImport.plg | 管轄内へ取り込む | 管轄内なら登録を実行。管轄外なら Save As での保存し直しを案内（下記の制限参照） |
| GitKeeperStatus.plg | 状態確認 | keeper.py status --open を起動し、結果ファイルを開く |
| GitKeeperSettings.plg | 設定 | 対象 repo の表示と、管轄外メッセージ抑制の解除（再表示） |

- ソースは `src/*.plg.txt`（UTF-8）。`generate_plg.py` で UTF-16BE + BOM の `.plg` を生成します（Drum Pattern Palette プロジェクトの符号化実績に倣っています）
- ソース内の `__REPO_ROOT__` と `__HOME__` は、setup.sh が配置時に実際の値へ置換します。template に入っている生成済み .plg はプレースホルダのままです

## 管轄外メッセージについて

文案は仕様書第6章の指定どおり、一字一句そのまま GitKeeperSave.plg に入っています。表示後に抑制フラグ（`~/.config/sibelius-git-keeper/suppress_ooj.flag`）を立て、以降は表示しません。再表示は GitKeeperSettings.plg（設定）から行えます。

## 初稿の制限

- **取り込みのフォルダ選択 UI は未実装**です。ManuScript からフォルダ選択ダイアログを開く API の有無が未確認のため、初稿では「名前を付けて保存で曲フォルダへ保存し直してから再実行」という案内方式にしています
- 状態確認は、外部コマンドの出力をプラグイン内ダイアログで表示するのではなく、keeper が書いたテキストファイルを開く方式です

## 実機で最初に裏取りする項目

1. .plg のファイル構造（メソッド定義の書式）が Sibelius に読み込まれるか、メニューにコマンドが出るか
2. 符号化 UTF-16BE + BOM と改行コードの扱い
3. `Sibelius.ActiveScore`、`Score.FileName`、`Score.SaveAs(path, 'SIBL')` の正確なシグネチャ
4. `Sibelius.LaunchApplication(path, params, hide)` の引数形式と、hide=True でフォーカスを奪わないこと
5. `Sibelius.FileExists` の有無
6. 文字列操作（連結 `&`、比較 `=` / `!=`、`Length`、`Substr` の添字の起点）
7. フォルダ選択ダイアログ API の有無（取り込み用）
8. プラグイン名／コマンド名の表示名をどこで決めるか（ファイル名か内部名か）
