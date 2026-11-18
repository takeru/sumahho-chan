/*
 * Rakuten Hand エージェント用「首」ファームウェア (ATOM Lite + ATOMIC Motion Base)
 * BLE 版 (2026-11-18 Wi-Fi/HTTP 版から書き換え)
 *
 * 役割: スマホの顔ページ(face.html)から BLE UART でコマンドを受けて
 *       パン/チルトのサーボ2個を動かす。角度制限・なめらか動作・
 *       ジェスチャ・ゆらぎは、micro:bit 版(neck-microbit.ts)と
 *       同じくこちら側で完結させる。
 *
 * 接続の流れ:
 *   agent.py → (http://127.0.0.1:8765/intent) → face.html
 *     → Web Bluetooth → このファーム → Motion Base 経由でサーボ
 *   face.html の「首とつなぐ (BLE)」ボタンで "HandAgent-Neck" を選ぶ。
 *
 * BLE: Nordic UART Service (micro:bit 版と同じ UUID 体系なので
 *      face.html は接続先を替えるだけで使える)
 *   Service : 6e400001-b5a3-f393-e0a9-e50e24dcca9e
 *   RX      : 6e400002-... (スマホ -> 首, Write) コマンド受信
 *   TX      : 6e400003-... (首 -> スマホ, Notify) 状態通知
 *   ペアリングは Just Works (認証なし)。家庭内の机の上での利用前提。
 *   Wi-Fi を使わないので secrets.h は不要になり、消費電力も下がる。
 *
 * 前提ライブラリ: M5Atom (ライブラリマネージャから "M5Atom" を入れる)
 *   - AtomMotion クラスを使う。ライブラリのバージョンにより
 *     API 名が異なる場合は AtomMotion のサンプルに合わせて直すこと。
 *   - BLE は ESP32 Arduino コア標準 (追加ライブラリ不要)。
 *
 * コマンドは neck-microbit.ts と共通 (改行区切り):
 *   PAN <0-180> / TILT <40-140> / LOOK <pan> <tilt> / CENTER /
 *   NOD / SHAKE / SWAY ON|OFF
 *
 * 配線: パンサーボ -> Motion Base のサーボch1 / チルトサーボ -> ch2
 *       サーボ電源は Motion Base 側 (電池/USB) から供給される
 */

#include <M5Atom.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

AtomMotion atomMotion;

#define SERVICE_UUID "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define RX_UUID "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
#define TX_UUID "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
#define DEVICE_NAME "HandAgent-Neck"

const int PAN_MIN = 10, PAN_MAX = 170;
const int TILT_MIN = 40, TILT_MAX = 140;
int panTarget = 90, tiltTarget = 90;
int panNow = 90, tiltNow = 90;
bool swayOn = true;
int gesture = 0;  // 0=なし 1=NOD 2=SHAKE
int gestureStep = 0;
unsigned long lastCommandAt = 0;
unsigned long lastStepAt = 0;

BLECharacteristic* txChar = nullptr;
volatile bool bleConnected = false;
volatile bool justConnected = false;
String rxBuffer = "";

int clampPan(int v) { return min(PAN_MAX, max(PAN_MIN, v)); }
int clampTilt(int v) { return min(TILT_MAX, max(TILT_MIN, v)); }

void notifyStatus() {
  if (!bleConnected || txChar == nullptr) return;
  String s = "ok pan=" + String(panNow) + " tilt=" + String(tiltNow);
  txChar->setValue(s.c_str());
  txChar->notify();
}

void handleCommand(String line) {
  line.trim();
  if (line.length() == 0) return;
  lastCommandAt = millis();
  int sp1 = line.indexOf(' ');
  String cmd = (sp1 < 0) ? line : line.substring(0, sp1);
  String rest = (sp1 < 0) ? "" : line.substring(sp1 + 1);
  if (cmd == "PAN") {
    panTarget = clampPan(rest.toInt());
  } else if (cmd == "TILT") {
    tiltTarget = clampTilt(rest.toInt());
  } else if (cmd == "LOOK") {
    int sp2 = rest.indexOf(' ');
    if (sp2 > 0) {
      panTarget = clampPan(rest.substring(0, sp2).toInt());
      tiltTarget = clampTilt(rest.substring(sp2 + 1).toInt());
    }
  } else if (cmd == "CENTER") {
    panTarget = 90; tiltTarget = 90;
  } else if (cmd == "NOD") {
    gesture = 1; gestureStep = 0;
  } else if (cmd == "SHAKE") {
    gesture = 2; gestureStep = 0;
  } else if (cmd == "SWAY") {
    swayOn = rest.startsWith("ON");
  }
  notifyStatus();
}

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* server) override {
    bleConnected = true;
    justConnected = true;
  }
  void onDisconnect(BLEServer* server) override {
    bleConnected = false;
    BLEDevice::startAdvertising();  // 切れたら再び発見可能にする
  }
};

class RxCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* ch) override {
    // 受信はバッファに貯めるだけ。行の処理は loop() 側で行う
    rxBuffer += ch->getValue().c_str();
  }
};

void setup() {
  M5.begin(true, false, true);  // Serial有効, I2Cは AtomMotion.Init 側, LED有効
  atomMotion.Init();
  atomMotion.SetServoAngle(1, panNow);
  atomMotion.SetServoAngle(2, tiltNow);

  BLEDevice::init(DEVICE_NAME);
  BLEDevice::setMTU(64);  // 1回の書き込みで長めのコマンドも受けられるように
  BLEServer* server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());
  BLEService* service = server->createService(SERVICE_UUID);

  BLECharacteristic* rxChar = service->createCharacteristic(
      RX_UUID, BLECharacteristic::PROPERTY_WRITE |
                   BLECharacteristic::PROPERTY_WRITE_NR);
  rxChar->setCallbacks(new RxCallbacks());

  txChar = service->createCharacteristic(TX_UUID,
                                         BLECharacteristic::PROPERTY_NOTIFY);
  txChar->addDescriptor(new BLE2902());

  service->start();
  BLEAdvertising* adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(SERVICE_UUID);  // face.html の services フィルタに一致させる
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
  Serial.println("neck ready: BLE '" DEVICE_NAME "' advertising");

  lastCommandAt = millis();
}

void loop() {
  // 受信バッファから改行区切りでコマンドを取り出す
  int nl;
  while ((nl = rxBuffer.indexOf('\n')) >= 0) {
    String line = rxBuffer.substring(0, nl);
    rxBuffer = rxBuffer.substring(nl + 1);
    handleCommand(line);
  }
  if (justConnected) {  // 接続の挨拶にうなずく
    justConnected = false;
    gesture = 1; gestureStep = 0;
  }

  unsigned long now = millis();
  if (now - lastStepAt < 20) return;
  lastStepAt = now;

  if (gesture == 1) {  // NOD
    const int seq[4] = {115, 90, 115, 90};
    tiltTarget = seq[gestureStep % 4];
    if (++gestureStep >= 12) gesture = 0;
  } else if (gesture == 2) {  // SHAKE
    const int seq[4] = {65, 115, 65, 90};
    panTarget = seq[gestureStep % 4];
    if (++gestureStep >= 12) gesture = 0;
  } else if (swayOn && now - lastCommandAt > 8000) {
    if (random(100) < 3) {
      panTarget = clampPan(90 + (int)random(-25, 26));
      tiltTarget = clampTilt(90 + (int)random(-8, 13));
    }
  }

  if (panNow < panTarget) panNow = min(panTarget, panNow + 2);
  if (panNow > panTarget) panNow = max(panTarget, panNow - 2);
  if (tiltNow < tiltTarget) tiltNow = min(tiltTarget, tiltNow + 1);
  if (tiltNow > tiltTarget) tiltNow = max(tiltTarget, tiltNow - 1);
  atomMotion.SetServoAngle(1, (uint8_t)panNow);
  atomMotion.SetServoAngle(2, (uint8_t)tiltNow);
}
