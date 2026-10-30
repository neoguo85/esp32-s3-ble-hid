/*
 * ESP32-S3 BLE → USB HID 键盘输入器
 *
 * Mac 上复制文本 → 通过 BLE 发给板子（存 LittleFS，断电不丢）→ 板子插到 Windows
 * 按 BOOT 键，用标准 USB 键盘逐字符敲出来。目标机零安装、零驱动。
 *
 * 协议（BLE UART Service，Nordic UUID）：
 *   Mac → 板子:  "LEN:<字节数>\n" 之后跟原始字节流（可任意分片）
 *   板子 → Mac:  "OK:<字节数>" / "ERR:len" / "ERR:mem"
 *   清空:        发送 "LEN:0\n"
 *
 * 板子: ESP32-S3 N16R8，USB CDC On Boot = Disabled
 */

#include "USB.h"
#include "USBHIDKeyboard.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <LittleFS.h>

USBHIDKeyboard Keyboard;

// ---------------- 可调参数 ----------------

#define BTN_BOOT 0
#define BLE_NAME "ESP32_HID_KEY"

static const int CHAR_DELAY = 80;   // 字符间隔 ms（远程桌面/VPC 漏字就调大）
static const int HOLD_DELAY = 20;   // 按键保持 ms
static const unsigned long DEBOUNCE = 300;
static const size_t MAX_TEXT = 64 * 1024;   // 上限 64KB（约 6 万字），再大容易 malloc 失败

#define SERVICE_UUID "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define RX_UUID      "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define TX_UUID      "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

// ---------------- 状态 ----------------

BLECharacteristic *pTx = nullptr;

char  *rxBuf = nullptr;      // BLE 接收缓冲
size_t rxLen = 0;            // 本包声明的长度
size_t rxGot = 0;            // 已收字节
volatile bool rxActive = false;
volatile bool rxDone   = false;
String hdr;                  // 头部拼接

char  *text = nullptr;       // 当前待打文本
size_t textLen = 0;

volatile size_t typeIndex = 0;
bool isTyping = false;
bool isPaused = false;
bool keyHeld  = false;
unsigned long typeTimer = 0;
unsigned long lastPress = 0;

// ---------------- 工具 ----------------

void notifyStr(const char *s) {
  if (!pTx) return;
  pTx->setValue((uint8_t *)s, strlen(s));
  pTx->notify();
}

bool isSupportedChar(char c) {
  if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')) return true;
  switch (c) {
    case '!': case '@': case '#': case '$': case '%': case '^': case '&': case '*':
    case '(': case ')': case '_': case '+': case '-': case '=': case '[': case ']':
    case '{': case '}': case ';': case '\'': case ':': case '"': case ',': case '.':
    case '/': case '<': case '>': case '?': case '\\': case '|': case '`': case '~':
    case ' ': case '\n': return true;
    default: return false;
  }
}

void releaseAllKeys() {
  Keyboard.releaseAll();
  keyHeld = false;
}

// ---------------- 文本存储 ----------------

void setText(char *buf, size_t len) {
  if (text) { free(text); text = nullptr; }
  text = buf;
  textLen = len;
  typeIndex = 0;
  Serial.printf("[text] %u bytes loaded\n", (unsigned)textLen);
}

bool saveToFlash(const char *buf, size_t len) {
  File f = LittleFS.open("/text.txt", "w");
  if (!f) { Serial.println("[fs] open for write failed"); return false; }
  size_t n = f.write((const uint8_t *)buf, len);
  f.close();
  Serial.printf("[fs] saved %u/%u\n", (unsigned)n, (unsigned)len);
  return n == len;
}

void loadFromFlash() {
  if (!LittleFS.begin(true)) {
    Serial.println("[fs] mount failed");
    return;
  }
  File f = LittleFS.open("/text.txt", "r");
  if (!f) { Serial.println("[fs] no saved text"); return; }
  size_t n = f.size();
  if (n == 0 || n > MAX_TEXT) { f.close(); return; }
  char *buf = (char *)malloc(n + 1);
  if (!buf) { f.close(); return; }
  size_t got = f.read((uint8_t *)buf, n);
  f.close();
  buf[got] = '\0';
  setText(buf, got);
}

// ---------------- BLE ----------------

class ServerCB : public BLEServerCallbacks {
  void onConnect(BLEServer *s) { Serial.println("[ble] connected"); }
  void onDisconnect(BLEServer *s) {
    Serial.println("[ble] disconnected");
    BLEDevice::startAdvertising();
  }
};

void appendRx(const char *p, size_t n) {
  if (!rxBuf || rxGot >= rxLen) return;
  size_t room = rxLen - rxGot;
  if (n > room) n = room;
  memcpy(rxBuf + rxGot, p, n);
  rxGot += n;
}

class RxCB : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *c) {
    String v = c->getValue();
    size_t n = v.length();
    if (n == 0) return;

    if (!rxActive) {
      hdr.concat(v);
      int nl = hdr.indexOf('\n');
      if (nl < 0) { if (hdr.length() > 48) hdr = ""; return; }

      String line = hdr.substring(0, nl);
      String rest = hdr.substring(nl + 1);
      hdr = "";
      line.trim();
      if (!line.startsWith("LEN:")) return;

      long want = line.substring(4).toInt();
      if (want < 0 || (size_t)want > MAX_TEXT) { notifyStr("ERR:len"); return; }

      if (rxBuf) { free(rxBuf); rxBuf = nullptr; }
      rxLen = (size_t)want;
      rxGot = 0;
      if (rxLen > 0) {
        rxBuf = (char *)malloc(rxLen + 1);
        if (!rxBuf) { notifyStr("ERR:mem"); return; }
      }
      rxActive = true;
      if (rest.length()) appendRx(rest.c_str(), rest.length());
    } else {
      appendRx(v.c_str(), n);
    }

    if (rxActive && rxGot >= rxLen) {
      rxActive = false;
      if (rxBuf) rxBuf[rxLen] = '\0';
      rxDone = true;
    }
  }
};

// ---------------- 打字 ----------------

void startTyping() {
  if (!text || textLen == 0) { Serial.println("[type] empty"); return; }
  releaseAllKeys();
  typeIndex = 0;
  isTyping = true;
  isPaused = false;
  keyHeld = false;
  typeTimer = millis() - CHAR_DELAY;
  Serial.println("[type] start");
}

void stopTyping() {
  isTyping = false;
  isPaused = false;
  releaseAllKeys();
  Serial.println("[type] stop");
}

void processTyping() {
  if (!isTyping || isPaused) return;

  unsigned long now = millis();

  if (typeIndex >= textLen) {
    isTyping = false;
    releaseAllKeys();
    Serial.println("[type] done");
    notifyStr("DONE");
    return;
  }

  if (!keyHeld) {
    if (now - typeTimer < (unsigned long)CHAR_DELAY) return;

    char c = text[typeIndex];

    if (c == '\r') { typeIndex++; typeTimer = now; return; }
    if (!isSupportedChar(c)) { typeIndex++; typeTimer = now; return; }

    if (c >= 'A' && c <= 'Z') {
      Keyboard.press(KEY_LEFT_SHIFT);
      Keyboard.press(c);
    } else if (c == '~') {
      Keyboard.press(KEY_LEFT_SHIFT);
      Keyboard.press('`');
    } else if (c == '|') {
      Keyboard.press(KEY_LEFT_SHIFT);
      Keyboard.press('\\');
    } else if (c == '\n') {
      Keyboard.press(KEY_RETURN);
    } else {
      Keyboard.press(c);
    }
    keyHeld = true;
    typeTimer = now;
  } else {
    if (now - typeTimer >= (unsigned long)HOLD_DELAY) {
      releaseAllKeys();
      keyHeld = false;
      typeIndex++;
      typeTimer = now;
    }
  }
}

// ---------------- setup / loop ----------------

void setup() {
  pinMode(BTN_BOOT, INPUT_PULLUP);
  Serial.begin(115200);
  USB.begin();
  Keyboard.begin();

  loadFromFlash();

  BLEDevice::init(BLE_NAME);
  BLEServer *srv = BLEDevice::createServer();
  srv->setCallbacks(new ServerCB());

  BLEService *svc = srv->createService(SERVICE_UUID);

  BLECharacteristic *rx = svc->createCharacteristic(
      RX_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  rx->setCallbacks(new RxCB());

  pTx = svc->createCharacteristic(TX_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  pTx->addDescriptor(new BLE2902());

  svc->start();

  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(SERVICE_UUID);
  adv->setScanResponse(true);
  adv->setMinPreferred(0x06);
  adv->setMinPreferred(0x12);
  BLEDevice::startAdvertising();

  Serial.printf("[ready] BLE name: %s, stored text: %u bytes\n", BLE_NAME, (unsigned)textLen);
}

void loop() {
  // BLE 收到的数据落到 Flash（不在 BLE 回调里做，避免阻塞协议栈）
  if (rxDone) {
    rxDone = false;
    size_t n = rxLen;
    if (n == 0) {
      if (text) { free(text); text = nullptr; }
      textLen = 0;
      typeIndex = 0;
      LittleFS.remove("/text.txt");
      Serial.println("[ble] cleared");
      notifyStr("OK:0");
    } else if (rxBuf) {
      saveToFlash(rxBuf, n);
      setText(rxBuf, n);
      rxBuf = nullptr;
      char ack[24];
      snprintf(ack, sizeof(ack), "OK:%u", (unsigned)n);
      notifyStr(ack);
    }
  }

  processTyping();

  unsigned long now = millis();
  if (digitalRead(BTN_BOOT) == LOW && (now - lastPress > DEBOUNCE)) {
    lastPress = now;
    if (isTyping) {
      isPaused = !isPaused;
      if (isPaused) releaseAllKeys();
      Serial.printf("[type] paused=%d\n", isPaused);
    } else {
      startTyping();
    }
  }

  delay(2);
}
