05/50 バグったシーン10種類
GitHub Actions＋Azure Speech クラウド作成パッケージ v2.0

【この方式でできること】
・WindowsでCMD、PowerShell、FFmpegを実行しません。
・Azureの正規バグっぺ音声をクラウド上で作成します。
・05/50のノート本文から音声を作り、10種類のバグったシーンを1本のMP4にします。
・初回だけAzureのキーとリージョンをGitHubへ登録します。
・2回目以降は、ブラウザで「Run workflow」を押すだけです。
・完成MP4はGitHubの画面からダウンロードします。

【完成MP4の仕様】
・2560×1440
・30fps
・H.264、CRF14
・AAC、256kbps、48kHz、ステレオ
・GitHubの成果物保存時の再圧縮なし（compression-level: 0）
・Azure Voice: en-GB-OllieMultilingualNeural
・rate: +14%
・pitch: +16%
・playback: 0.75
・gainMultiplier: 1.0

============================================================
初回だけ行う設定
============================================================

1. GitHubで非公開リポジトリを作る

GitHubへログインし、「New repository」を押します。
Repository nameは任意です。
「Private」を選ぶことを推奨します。
README、.gitignore、Licenseは追加せず、空のリポジトリを作ります。

2. このZIPの中身をアップロードする

このZIPを展開します。
GitHubの「uploading an existing file」を押し、展開したフォルダーの中身を
すべてドラッグ＆ドロップします。

重要：最上位に、次のフォルダーとファイルが見える状態にします。
・.github
・assets
・builder
・config
・input
・README_GITHUB設定手順.txt

「Commit changes」を押します。

3. Azure SpeechのキーをGitHub Secretへ登録する

リポジトリの次の画面を開きます。
Settings → Secrets and variables → Actions → New repository secret

1つ目
Name: AZURE_SPEECH_KEY
Secret: Azure SpeechリソースのKey 1またはKey 2

2つ目
Name: AZURE_SPEECH_REGION
Secret: Azure Speechリソースのリージョン名
例: japaneast

注意：
・キーはREADME、config、ソースコードへ書かないでください。
・キーをチャットへ貼らないでください。
・GitHub Secretへ一度登録すれば、通常は再入力不要です。
・Azureポータルに表示されるリージョン名を、英字の識別名で登録します。

4. GitHub Actionsを有効にする

リポジトリ上部の「Actions」を開きます。
確認画面が出た場合は、当該リポジトリのActionsを有効にします。

============================================================
動画を作るたびに行う操作
============================================================

1. GitHubの「Actions」を開きます。
2. 左側の「05-50 バグったシーン10種類を作成」を選びます。
3. 右側の「Run workflow」を押します。
4. 緑色の「Run workflow」を押します。
5. 実行行を開き、すべて緑のチェックになるまで待ちます。
6. 実行結果画面の下部にある「Artifacts」を開きます。
7. 「05-50_バグったシーン10種類_Azure正規音声」をダウンロードします。
8. ダウンロードしたZIPを展開すると、MP4と検査レポートがあります。

成果物の保存期間は14日です。必要なMP4は期限内にダウンロードしてください。

============================================================
エラーが出た場合
============================================================

【AZURE_SPEECH_KEY が未登録】
Secret名が完全に「AZURE_SPEECH_KEY」になっているか確認します。

【AZURE_SPEECH_REGION が未登録】
Secret名が完全に「AZURE_SPEECH_REGION」になっているか確認します。
値は例として「japaneast」のような英字のリージョン識別名です。

【Azure 401 / 403】
キーとリージョンが同じAzure Speechリソースのものか確認します。
キーを再発行した場合は、GitHub Secretも更新します。

【入力PPTXが検証済み版と一致しない】
このパッケージは、同梱した05/50のPPTX専用です。
input/source_05-50.pptxだけを別ファイルへ差し替えると、安全のため停止します。
別のPPTXへ変更するときは、仕様・素材・ノート・ハッシュを一緒に更新します。

【動画生成に失敗】
実行結果画面のArtifactsに「失敗時の検査記録」があればダウンロードします。
BUILD_ERROR.txtまたはlast_ffmpeg_error.txtに、停止した理由が記録されます。
Azureのキーそのものは記録されません。

============================================================
音声キャッシュについて
============================================================

最初の成功時に、Azureで作った音声をGitHub Actionsのキャッシュへ保存します。
PPTX、設定、ビルダーが同じなら、次回はキャッシュを使うため処理時間とAzure利用量を減らせます。
PPTX、設定、ビルダーのいずれかが変わると、新しい音声として作り直します。

