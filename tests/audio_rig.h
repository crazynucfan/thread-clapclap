#pragma once
#include <cmath>
#include <vector>
#include <stdexcept>
#include "../firmware/ThreadClapClap/AudioFeatures.h"
template <typename Detector>
struct AudioRig {
  AudioAnalyzer analyzer;
  Detector detector;
  uint32_t random = 0x12345678;
  unsigned events = 0, accepted = 0, rejected = 0;
  std::vector<typename Detector::Decision> decisions;

  AudioRig() { analyzer.reset(); detector.reset(0); }
  float noise() {
    random ^= random << 13; random ^= random >> 17; random ^= random << 5;
    return static_cast<float>(random & 65535) / 32767.5f - 1;
  }
  void sample(float ac, float dc = 2048) {
    const auto raw = static_cast<uint16_t>(std::fmax(0.0f, std::fmin(4095.0f, dc + ac)));
    if (!analyzer.push(raw)) return;
    const auto result = detector.process(analyzer.features(), analyzer.timeMs());
    if (result.doubleClap) ++events;
    if (result.decision == Detector::Decision::Accepted) ++accepted;
    if (result.decision != Detector::Decision::None && result.decision != Detector::Decision::Calibrated) {
      decisions.push_back(result.decision);
      if (result.decision != Detector::Decision::Accepted) ++rejected;
    }
  }
  void quiet(unsigned ms, float amplitude = 3) {
    for (unsigned i = 0; i < ms * 16; ++i) sample(noise() * amplitude);
  }
  void warm() { quiet(2300); if (!detector.calibrated()) throw std::runtime_error("calibration did not complete"); }
  void clap(float amplitude = 1200) {
    for (unsigned i = 0; i < 24 * 16; ++i) {
      const float envelope = std::exp(-static_cast<float>(i) / 90);
      sample(noise() * (3 + amplitude * envelope));
    }
  }
  void tone(float frequency, unsigned ms = 24, float amplitude = 800) {
    for (unsigned i = 0; i < ms * 16; ++i) {
      sample(amplitude * std::sin(2 * 3.14159265359f * frequency * i / AudioAnalyzer::sampleRate));
    }
  }
};
