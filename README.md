# スマッホチャン (sumahho-chan)

楽天ハンド (Rakuten Hand P710) を頭脳と顔にした、机の上に住む小さなAIエージェント。
首はパン・チルトだけの2軸で、AIから BLE 経由で向きを命令できる。

**開発日記 (GitHub Pages)**: https://takeru.github.io/sumahho-chan/
（機構シミュレーターもそこで動かせます）

## 構成

- **頭脳**: 楽天ハンド (P710) + Termux + Python (`agent.py`)
  - 聞く: Android の音声認識 / 考える: Claude API / 話す: Android TTS
  - 顔は単一HTMLのフルスクリーンUI (`face.html`)、意図キュー経由で首と連動
- **首**: ATOM Lite + ATOMIC Motion Base v1.2 + サーボ×2 (パン: SG90 / チルト: MG90S)
  - ファームウェア `neck-atom/neck-atom.ino` は BLE UART ペリフェラル (名前: HandAgent-Neck)
  - 旧案の MakeCode 版は `neck-microbit.ts`
- **土台**: Ø104 の円形ベースにスタックとパンサーボを内蔵 (全高 約202mm)

## ドキュメント

- `SETUP.md` — 端末のセットアップ手順 (Termux 導入から初回会話まで)
- `NECK.md` — 首の配線・BLE プロトコル・コマンド
- `MECHANISM.md` — 機構設計 (寸法・トルク計算・干渉チェック)
- `mechanism.svg` — 機構図
- `simulator.html` — 機構シミュレーター (依存なし単一HTML、ブラウザでそのまま開ける)

## CAD

寸法の正本は `params.json`。`build.py` を実行すると、CAD の JSON と STL・
シミュレーター・部品表・MECHANISM.md の主要寸法表・機構図が全部生成され、最後に組立チェックが走る。
寸法を変えるときは `params.json` だけを直して再実行する（生成物を手で直さない）。

```sh
python3 build.py              # 生成 + 組立チェック
python3 build.py --no-check   # 生成だけ
python3 check_assembly.py     # 組立チェックだけ
```

[SolidCraft](https://github.com/tangency-labs/solidcraft) の CLI（`solidcraft-cli` 0.44.1 で確認）が必要。
PATH にあればそれを使い、別の場所なら環境変数 `SOLIDCRAFT` で指定する。

- `cad/fit-gauge-servo.json` — サーボポケットのクリアランス確認ゲージ（生成物）
- `cad/parts/*.json` — 印刷部品 8 点：土台シェル / 底板 / パンデッキ / 頬板 (右・左) / 背面ボックス / 裏蓋 / 中空ピン（生成物）
- `check_assembly.py` — 部品と購入品のダミーを組立位置に並べ、干渉・隙間・ネジやサーボの通り道・浮いた塊を確認する（MECHANISM.md §8）

STL と `.sc` は `build.py` が `cad/` に出力する（生成物なのでリポジトリには含めていない）。プレビュー画像は:

```sh
solidcraft-cli snapshot cad/parts/base-shell.json --out base-shell.png --view iso
```

サーボ寸法は実在モデルの測定に基づく (出典: Printables の SG90 モデル、
GitHub `ledalert/cadmodel-sg90`)。第三者のモデルファイル自体は再配布しないため
`cad/servo/` は含めていない。

## 状態

設計・シミュレーター・CAD モデルまで完了。**実機では未検証**
(端末セットアップ、ファームウェアのコンパイル確認、部品の印刷・組み立てはこれから)。

## ライセンス

MIT
