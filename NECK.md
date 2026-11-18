# 首（AIが制御できる台座）の作り方

方針（2026-11-18 決定）：既製品の追尾スタンドはAIから命令できないので、
首は自作する。楽天ハンド＝顔と頭脳、サーボ2個＝首。

## コントローラは2案（コマンド体系は共通）
- **A案（本命）：ATOM Lite（手持ち）＋ ATOMIC Motion Base v1.2（¥1,606、電力モニター付き）**
  - ファーム：`neck-atom/neck-atom.ino`（Arduino / M5Atom ライブラリの AtomMotion、BLE は ESP32 コア標準）
  - 接続：**BLE UART**（2026-11-18 に Wi-Fi/HTTP から変更）。スマホ側は face.html が
    Web Bluetooth で中継する（micro:bit 版と同じ UUID 体系）。デバイス名は `HandAgent-Neck`
  - Wi-Fi を使わないので SSID/パスワード設定は不要。消費電力も下がる
  - ペアリングは Just Works（認証なし）。家庭内利用の前提
  - サーボ配線：パン→サーボch1、チルト→サーボch2。サーボ電源は Motion Base 側
    （電池 18350 900mAh / USB 給電）から供給される
  - 在庫メモ：旧版 A090 は売り切れ・キット K053 は販売終了。買うのは v1.2（A090-V12）
- **B案：micro:bit（手持ち）**
  - ファーム：`neck-microbit.ts`（MakeCode、bluetooth 拡張が必要）
  - 接続：BLE UART。スマホ側は face.html が Web Bluetooth で中継する
  - 配線：パン信号→P0、チルト信号→P1、サーボ電源は基板の5V等（3Vピン不可、GND共通）

## 全体像（A案）
```
Claude API ⇄ agent.py (Termux/Python, スマホ内)
               │ 意図キュー (http://127.0.0.1:8765/intent)
               ▼
            face.html (スマホの Chrome)
               │ Web Bluetooth (BLE UART)
               ▼
            ATOM Lite + Motion Base (neck-atom.ino)
               │ サーボ ch1=パン / ch2=チルト
               ▼
            サーボ2個 + 台座に載った楽天ハンド
```
- agent は「NOD」「LOOK 60 90」のような**意図だけ**出す
- 角度制限・なめらかな動き・待機中のゆらぎは首側で処理（反射系をクラウドに任せない原則どおり）
- face.html は顔表示と BLE 転送を担当。Web Bluetooth は localhost がセキュアコンテキスト扱いなので、agent.py が配信する http://127.0.0.1:8765/ で開く

## 部品
- サーボ SG90 相当 x2（パン用・チルト用）
- スマホを固定する台座/雲台（パンサーボのホーンに載せる板＋スマホホルダー。3Dプリントでも工作でも可）
- A案：ATOM Lite（手持ち）＋ ATOMIC Motion Base v1.2
- B案：micro:bit（手持ち）＋モータードライバー基板 or ブレークアウト（サーボ電源の取り出し用）、サーボ用電源は基板の5V出力、なければ USB 5V を分岐（**micro:bit の 3V ピンからは取らない**、GND は共通）

## 配線（B案のみ。A案はサーボを Motion Base に挿すだけ）
- パンサーボ信号 → P0、チルトサーボ信号 → P1
- サーボ電源(+/-) → 5V / GND（GND は micro:bit と共通）

## micro:bit 側の準備
1. MakeCode（https://makecode.microbit.org）で新規プロジェクト
2. `neck-microbit.ts` の内容を JavaScript 編集画面に貼る
3. 拡張機能で **bluetooth** を追加（プロジェクト設定で Bluetooth を有効化、ペアリングは Just Works 側）
4. ダウンロードして micro:bit に書き込む

## スマホ側の起動順
1. Termux で `python agent.py`（SETUP.md の手順が済んでいる前提）
2. スマホの Chrome で http://127.0.0.1:8765/ を開く → 顔が出る
3. 「首とつなぐ (BLE)」ボタンを押して接続先を選ぶ（A案は `HandAgent-Neck`、B案は micro:bit。接続時に CENTER が送られ、A案は挨拶にうなずく）
4. 画面タップ（左/右/中央）でも首が動けば配線OK。本番は会話に連動（返事のときに NOD など）

## コンパクト設計目標（2026-11-18 ユーザー希望）
全体を小さくまとめる。目標フットプリントは土台 Ø約80mm、顔（楽天ハンド）を立てて載せる形。

```
        ┌─────────┐
        │ 楽天ハンド │  顔 63×138mm / 134g
        │  (顔)    │
        └────┬────┘
          [チルト軸]  ← サーボch2。スマホの重心を軸の近くに置く
        ┌────┴────┐
        │ パン軸    │  ← サーボch1（縦軸回転）
        │ ATOM Lite │
        │ MotionBase│  24×48mm のスタックがそのまま入る
        └─────────┘
         土台 Ø80mm（中に電源分岐とケーブルを収納）
```
- サーボは SG90 級 x2 で十分（スマホ134g、重心をチルト軸近くに寄せればトルク余裕あり）
- 給電USB-Cは土台から短く引き回してスマホへ（垂れ下がるケーブルをなくす）
- これ以上小さくするなら顔を ATOMS3R（1.28インチ）に替えるスタックチャン実寸（約65mm級）になるが、顔の表現力と引き換え。当面は楽天ハンド案で進める

## コマンド一覧（agent → 首）
| コマンド | 意味 |
|---|---|
| `PAN 0-180` | 左右（90=正面、クランプ 10-170） |
| `TILT 40-140` | 上下（90=水平） |
| `LOOK <pan> <tilt>` | 同時指定 |
| `CENTER` | 正面に戻る |
| `NOD` / `SHAKE` | うなずく / 首を振る |
| `SWAY ON/OFF` | 待機中のゆらぎ（8秒無操作で小さく向きを変える） |

## ハマりどころ
- BLE の接続先に出てこない：A案は ATOM の電源とファーム書き込みを確認（シリアルに "neck ready" が出ているか）。B案は MakeCode 側で bluetooth 拡張が有効か、micro:bit のペアリングモード設定を確認
- サーボが震える/弱い：電源不足。5V を別系統から取る
- 顔ページで BLE ボタンが出ない：Chrome で開いているか（Web Bluetooth は Chrome のみ）、URL が 127.0.0.1 かを確認
