#pragma once

#include "ClapDetector.h"
#include "AudioClapDetector.h"

namespace config {
// Analog FFT filtering is the default. Set false to use the original DOUT mode.
constexpr bool useAnalogAudio = true;
constexpr uint8_t audioPin = 0;  // XIAO D0 / GPIO0 <- Waveshare AOUT
// XIAO D1 = GPIO1. Set to 0 if you used D0 from the earlier wiring plan.
constexpr uint8_t soundPin = 1;
constexpr uint8_t resetButtonPin = 9;  // XIAO BOOT; hold after normal startup
constexpr uint32_t factoryResetHoldMs = 5000;
constexpr uint32_t startupQuietMs = 1500;
constexpr ClapDetector::Config detector{40, 120, 160, 600};
constexpr AudioClapDetector::Config audioDetector{};
static_assert(!useAnalogAudio || audioPin != soundPin, "AOUT and DOUT must use separate pins in analog mode");
static_assert(detector.quietMs < detector.minClapGapMs, "Quiet period must fit between claps");
static_assert(detector.maxBurstMs < detector.minClapGapMs, "A burst must be shorter than the clap gap");
static_assert(detector.minClapGapMs < detector.sequenceGapMs, "Clap window must allow two claps");
}  // namespace config
