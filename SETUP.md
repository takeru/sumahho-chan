# 楽天ハンド（P710）エージェント化 初日セットアップ

頭脳は Claude API 中心（2026-11-18 決定）。ローカルLLMは後の実験枠。

## 0. 実機の確認（5分）
- 設定 → デバイス情報：Androidバージョンをメモ
- バッテリーの膨張がないか目視（背面が浮いていたら常時給電は中止して相談）
- Wi-Fi に接続、充電しながら作業

## 1. Termux を入れる（Play ストア版は古いので使わない）
- ブラウザで F-Droid（https://f-droid.org）を開き、F-Droid アプリをインストール
  （提供元不明アプリの許可が要る）
- F-Droid から **Termux** と **Termux:API**、ついでに **Termux:Boot**（フェーズ2で使う）を入れる
  ※ Termux 本体とアドオンは同じ配布元（F-Droid）で揃えること。混ぜると動かない

## 2. Termux の初期設定
Termux を開いて順に実行：
```
pkg update
pkg install python termux-api
termux-setup-storage
```
マイク許可の確認：
```
termux-speech-to-text
```
→ 何か話して、文字が返れば音声認識OK。
```
termux-tts-speak "テストです"
```
→ 声が出ればTTS OK（日本語の声が出ない場合は、設定 → 言語と入力 → テキスト読み上げ で日本語音声を有効化）

## 3. エージェント本体を入れる
- agent.py を端末に持ってくる（USB転送、または自宅LAN/Tailscale経由のコピー）
- APIキーは**端末上でのみ**入力する（チャットやメモに貼らない）：
```
export ANTHROPIC_API_KEY=（自分のキー）
python agent.py
```
- Enter → 話す → Claude が短く返事して読み上げ、まで一周したら MVP 完了

## 4. うまくいかないとき
- 音声認識が空振りする：静かな場所で、端末から30cmくらいで話す。Googleアプリの音声入力が有効か確認
- TTSが英語になる：テキスト読み上げの優先エンジンと言語を日本語に
- APIエラー：キーの綴りと、端末の日時設定（自動）が合っているか確認

## 次のフェーズ（別途）
- 顔UI（単一HTML）＋キオスク常駐、Termux:Boot で起動時オートスタート
- 定時で自分から話しかける（朝の挨拶など）
