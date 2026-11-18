// Rakuten Hand エージェント用「首」ファームウェア (micro:bit / MakeCode TypeScript)
//
// 役割: スマホ(顔UI)から BLE UART または USBシリアルでコマンドを受けて
//       パン/チルトのサーボ2個を動かす。反射系(角度制限・なめらか動作・
//       ゆらぎ)はこちら側で完結させ、スマホ側は意図だけ送る。
//
// 拡張機能: Bluetooth が必要。MakeCode でこのファイルを読み込み、
//           拡張機能に bluetooth を追加すること(プロジェクト設定で
//           "Bluetooth" を有効化。ペアリングは Just Works)。
//
// 配線: パンサーボ信号 -> P0 / チルトサーボ信号 -> P1
//       サーボ電源はモータードライバ基板の5V等から取り、GNDは共通にする
//       (micro:bit の 3V ピンからサーボ電源を取らないこと)
//
// コマンド(改行区切り、BLE UART / シリアル共通):
//   PAN <0-180>     左右の絶対角度(90が正面)
//   TILT <40-140>   上下の絶対角度(90が水平)
//   LOOK <pan> <tilt>
//   CENTER          正面に戻る
//   NOD             うなずく(2回)
//   SHAKE           首を振る(2往復)
//   SWAY ON|OFF     待機中のゆらぎ(生きてる感)のON/OFF

let panTarget = 90
let tiltTarget = 90
let panNow = 90
let tiltNow = 90
let swayOn = true
let gesture = 0 // 0=なし 1=NOD 2=SHAKE
let gestureStep = 0
let lastCommandAt = 0

const PAN_MIN = 10
const PAN_MAX = 170
const TILT_MIN = 40
const TILT_MAX = 140

function clampPan(v: number): number {
    return Math.min(PAN_MAX, Math.max(PAN_MIN, v))
}
function clampTilt(v: number): number {
    return Math.min(TILT_MAX, Math.max(TILT_MIN, v))
}

function handleCommand(line: string) {
    lastCommandAt = input.runningTime()
    let parts = line.split(" ")
    let cmd = parts[0]
    if (cmd == "PAN" && parts.length >= 2) {
        panTarget = clampPan(parseInt(parts[1]))
    } else if (cmd == "TILT" && parts.length >= 2) {
        tiltTarget = clampTilt(parseInt(parts[1]))
    } else if (cmd == "LOOK" && parts.length >= 3) {
        panTarget = clampPan(parseInt(parts[1]))
        tiltTarget = clampTilt(parseInt(parts[2]))
    } else if (cmd == "CENTER") {
        panTarget = 90
        tiltTarget = 90
    } else if (cmd == "NOD") {
        gesture = 1
        gestureStep = 0
    } else if (cmd == "SHAKE") {
        gesture = 2
        gestureStep = 0
    } else if (cmd == "SWAY" && parts.length >= 2) {
        swayOn = parts[1] == "ON"
    }
}

bluetooth.startUartService()
bluetooth.onUartDataReceived(serial.delimiters(Delimiters.NewLine), function () {
    handleCommand(bluetooth.uartReadUntil(serial.delimiters(Delimiters.NewLine)))
})
serial.onDataReceived(serial.delimiters(Delimiters.NewLine), function () {
    handleCommand(serial.readUntil(serial.delimiters(Delimiters.NewLine)))
})
bluetooth.onBluetoothConnected(function () {
    basic.showIcon(IconNames.Happy)
})
bluetooth.onBluetoothDisconnected(function () {
    basic.showIcon(IconNames.Asleep)
})

basic.showIcon(IconNames.Square)
lastCommandAt = input.runningTime()

basic.forever(function () {
    // ジェスチャは目標角度の列として処理する(ブロックしない)
    if (gesture == 1) { // NOD: 下を向いて戻る x2
        let seq = [115, 90, 115, 90]
        tiltTarget = seq[gestureStep % seq.length]
        gestureStep += 1
        if (gestureStep >= seq.length * 3) gesture = 0
    } else if (gesture == 2) { // SHAKE: 左右 x2
        let seq = [65, 115, 65, 90]
        panTarget = seq[gestureStep % seq.length]
        gestureStep += 1
        if (gestureStep >= seq.length * 3) gesture = 0
    } else if (swayOn && input.runningTime() - lastCommandAt > 8000) {
        // 8秒以上コマンドが無ければ、たまに小さく向きを変える(ゆらぎ)
        if (Math.randomRange(0, 99) < 3) {
            panTarget = clampPan(90 + Math.randomRange(-25, 25))
            tiltTarget = clampTilt(90 + Math.randomRange(-8, 12))
        }
    }

    // 目標へ少しずつ寄せる(なめらかさ担当)
    if (panNow < panTarget) panNow = Math.min(panTarget, panNow + 2)
    if (panNow > panTarget) panNow = Math.max(panTarget, panNow - 2)
    if (tiltNow < tiltTarget) tiltNow = Math.min(tiltTarget, tiltNow + 1)
    if (tiltNow > tiltTarget) tiltNow = Math.max(tiltTarget, tiltNow - 1)
    pins.servoWritePin(AnalogPin.P0, panNow)
    pins.servoWritePin(AnalogPin.P1, tiltNow)
    basic.pause(20)
})
