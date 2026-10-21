#include <Arduino.h>
#include <Matter.h>
#include <sdkconfig.h>

#if !defined(CONFIG_IDF_TARGET_ESP32C6)
#error "Select XIAO_ESP32C6. This example targets the ESP32-C6 native Thread radio."
#endif
#if !CONFIG_ENABLE_MATTER_OVER_THREAD || !CONFIG_ENABLE_CHIPOBLE
#error "This example requires Matter over Thread with BLE commissioning."
#endif
#if !CONFIG_OPENTHREAD_FTD
#error "A Full Thread Device build is required for Thread router eligibility."
#endif

#include <esp_openthread.h>
#include <esp_openthread_lock.h>
#include <openthread/thread.h>
#include <openthread/thread_ftd.h>
#include <platform/CHIPDeviceLayer.h>

#include "Config.h"

MatterGenericSwitch clapSwitch;
ClapDetector detector(config::detector);

// Capture both edges: even very short LOW pulses reach the detector. No logging
// or Matter calls in the ISR. Drop the whole partial gesture on queue overflow.
struct SoundEdge {
  uint32_t ms;
  bool active;
};
constexpr uint16_t edgeCapacity = 128;
volatile SoundEdge edges[edgeCapacity];
volatile uint16_t edgeHead = 0;
volatile uint16_t edgeTail = 0;
volatile bool edgeOverflow = false;
portMUX_TYPE edgeMux = portMUX_INITIALIZER_UNLOCKED;

void ARDUINO_ISR_ATTR captureSoundEdge() {
  portENTER_CRITICAL_ISR(&edgeMux);
  const uint16_t next = (edgeHead + 1) % edgeCapacity;
  if (next == edgeTail) {
    edgeOverflow = true;
  } else {
    edges[edgeHead].ms = millis();
    edges[edgeHead].active = digitalRead(config::soundPin) == LOW;
    edgeHead = next;
  }
  portEXIT_CRITICAL_ISR(&edgeMux);
}

bool routerConfigured = false;
bool resetHeld = false;
bool resetRequested = false;
uint32_t resetStarted = 0;
uint32_t detectionStarted = 0;
uint32_t lastStatus = 0;
bool lastReady = false;
uint32_t droppedBatches = 0;

static void fatal(const char *message) {
  Serial.println(message);
  while (true) {
    delay(1000);
  }
}

static const char *roleName(otDeviceRole role) {
  switch (role) {
    case OT_DEVICE_ROLE_DISABLED: return "disabled";
    case OT_DEVICE_ROLE_DETACHED: return "detached";
    case OT_DEVICE_ROLE_CHILD: return "child";
    case OT_DEVICE_ROLE_ROUTER: return "router";
    case OT_DEVICE_ROLE_LEADER: return "leader";
    default: return "unknown";
  }
}

static void updateThreadStatus() {
  // Matter owns OpenThread initialization. Do not start a second OT stack,
  // create a dataset, or force a router role: join the phone's existing network.
  if (!esp_openthread_lock_acquire(pdMS_TO_TICKS(20))) {
    return;
  }
  otInstance *instance = esp_openthread_get_instance();
  if (instance == nullptr) {
    esp_openthread_lock_release();
    return;
  }
  otError error = OT_ERROR_NONE;
  if (!routerConfigured) {
    otLinkModeConfig mode = otThreadGetLinkMode(instance);
    mode.mRxOnWhenIdle = true;
    mode.mDeviceType = true;
    mode.mNetworkData = true;
    error = otThreadSetLinkMode(instance, mode);
    if (error == OT_ERROR_NONE) {
      error = otThreadSetRouterEligible(instance, true);
    }
    routerConfigured = error == OT_ERROR_NONE;
  }
  const otDeviceRole role = otThreadGetDeviceRole(instance);
  const bool eligible = otThreadIsRouterEligible(instance);
  esp_openthread_lock_release();
  Serial.printf("Thread: role=%s router_eligible=%s config_error=%u\n",
                roleName(role), eligible ? "yes" : "no", static_cast<unsigned>(error));
}

static void reportDoubleClap(bool ready) {
  if (!ready) {
    Serial.println("Double clap detected; offline, event discarded.");
    return;
  }
  Serial.println("Double clap -> Matter button click (InitialPress + ShortRelease).");
  clapSwitch.click();
}

static void processSound(bool ready) {
  SoundEdge batch[edgeCapacity];
  uint16_t size = 0;
  // Snapshot time and pin level with the queue, preserving timestamp order.
  portENTER_CRITICAL(&edgeMux);
  const bool overflow = edgeOverflow;
  edgeOverflow = false;
  while (edgeTail != edgeHead) {
    batch[size++] = {edges[edgeTail].ms, edges[edgeTail].active};
    edgeTail = (edgeTail + 1) % edgeCapacity;
  }
  const uint32_t now = millis();
  const bool active = digitalRead(config::soundPin) == LOW;
  portEXIT_CRITICAL(&edgeMux);

  if (overflow || now - detectionStarted < config::startupQuietMs || ready != lastReady) {
    detector.reset(now, active);
    if (overflow) {
      ++droppedBatches;
      Serial.println("Sound edge queue overflow: gesture discarded; reduce sensor gain/sensitivity.");
    }
    lastReady = ready;
    return;
  }
  const uint32_t before = detector.acceptedClaps();
  for (uint16_t i = 0; i < size; ++i) {
    if (detector.update(batch[i].ms, batch[i].active)) {
      reportDoubleClap(ready);
    }
  }
  if (detector.update(now, active)) {
    reportDoubleClap(ready);
  }
  if (detector.acceptedClaps() != before) {
    Serial.printf("Clap: pending=%u accepted_total=%lu\n", detector.pendingClaps(),
                  static_cast<unsigned long>(detector.acceptedClaps()));
  }
}

static void handleFactoryReset() {
  const uint32_t now = millis();
  if (digitalRead(config::resetButtonPin) != LOW) {
    resetHeld = false;
  } else if (!resetHeld) {
    resetHeld = true;
    resetStarted = now;
  } else if (!resetRequested && now - resetStarted >= config::factoryResetHoldMs) {
    resetRequested = true;
    Serial.println("BOOT held 5s: erasing Matter fabrics and Thread credentials; restarting.");
    Matter.decommission();
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);  // bounded: standalone USB power must work without a serial host
  pinMode(config::soundPin, INPUT_PULLUP);
  pinMode(config::resetButtonPin, INPUT_PULLUP);
  Serial.printf("\nThread ClapClap: double-clap Matter button, XIAO ESP32-C6 / sound GPIO%u.\n", config::soundPin);

  if (!Matter.selectNetwork(MATTER_NETWORK_THREAD)) {
    fatal("FATAL: Matter Thread transport selection failed.");
  }
  if (!clapSwitch.begin()) {
    fatal("FATAL: could not create the Matter Generic Switch endpoint.");
  }
  Matter.setDeviceName("Thread ClapClap");
  Matter.setProductName("Thread ClapClap");
  Matter.begin();
  const String pairingCode = Matter.getManualPairingCode();
  if (pairingCode.isEmpty()) {
    fatal("FATAL: Matter startup failed; inspect the preceding SDK log.");
  }
  Serial.printf("Generic Switch endpoint: %u\n", clapSwitch.getEndPointId());
  if (!Matter.isDeviceCommissioned()) {
    Serial.println("Pair with Home Assistant Companion -> Add Matter device.");
    Serial.println("Manual pairing code: " + pairingCode);
    Serial.println("QR code URL: " + Matter.getOnboardingQRCodeUrl());
  }
  updateThreadStatus();
  detectionStarted = millis();
  detector.reset(detectionStarted, digitalRead(config::soundPin) == LOW);
  attachInterrupt(digitalPinToInterrupt(config::soundPin), captureSoundEdge, CHANGE);
  Serial.println("Allow 1.5s to settle, then clap twice, 160-599ms apart. Single claps do nothing.");
}

void loop() {
  // Protect Matter's state reads against the CHIP task. Sound detection keeps
  // running during commissioning and disconnection; offline events are dropped.
  chip::DeviceLayer::PlatformMgr().LockChipStack();
  const bool commissioned = Matter.isDeviceCommissioned();
  const bool ready = commissioned && Matter.isThreadConnected();
  chip::DeviceLayer::PlatformMgr().UnlockChipStack();
  processSound(ready);
  handleFactoryReset();
  const uint32_t now = millis();
  if (now - lastStatus >= 10000) {
    lastStatus = now;
    Serial.printf("Matter: commissioned=%s connected=%s DOUT=%s overflows=%lu\n",
                  commissioned ? "yes" : "no", ready ? "yes" : "no",
                  digitalRead(config::soundPin) == LOW ? "LOW" : "HIGH",
                  static_cast<unsigned long>(droppedBatches));
    updateThreadStatus();
  }
  delay(1);
}
