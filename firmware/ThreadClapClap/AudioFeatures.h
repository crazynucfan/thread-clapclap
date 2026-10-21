#pragma once

#include <cmath>
#include <stddef.h>
#include <stdint.h>

struct AudioFeatures {
  float rms = 0;
  float peak = 0;
  float highRatio = 0;      // 1.5-7.5 kHz power / 250 Hz-7.5 kHz power
  float concentration = 0;  // largest three neighbouring FFT bins / band power
  float flatness = 0;       // geometric / arithmetic mean of in-band bin power
  uint16_t occupiedBins = 0; // in-band bins holding at least 1/128 of band power
  float clippedFraction = 0;
  float rawMean = 0;
  uint16_t rawMin = 0;
  uint16_t rawMax = 0;
};

// ESP32-C6 has no floating-point unit. Keep the streaming filter, window, FFT,
// and per-bin statistics in fixed point; convert only final features to floats.
// 256 samples at 16kHz, 50% overlap: one spectrum / 8ms, 62.5Hz bin spacing.
class AudioAnalyzer {
 public:
  static constexpr size_t size = 256;
  static constexpr size_t hop = size / 2;
  static constexpr uint32_t sampleRate = 16000;

  AudioAnalyzer() {
    // Computed once before acquisition starts, never in the streaming path.
    for (size_t i = 0; i < size; ++i) {
      window_[i] = static_cast<int16_t>((0.5f - 0.5f * std::cos(2 * pi * i / (size - 1))) * 32767);
      logFraction_[i] = static_cast<uint16_t>(std::log2(1.0f + i / 256.0f) * 256);
    }
    for (size_t i = 0; i < size / 2; ++i) {
      twiddleR_[i] = static_cast<int16_t>(std::cos(-2 * pi * i / size) * 32767);
      twiddleI_[i] = static_cast<int16_t>(std::sin(-2 * pi * i / size) * 32767);
    }
  }

  void reset() {
    cursor_ = filled_ = sinceFrame_ = 0;
    samples_ = 0;
    initialized_ = false;
    previous_ = filteredQ8_ = 0;
    features_ = {};
  }

  bool push(uint16_t raw) {
    if (!initialized_) {
      previous_ = raw;  // don't turn the microphone's DC bias into an impulse
      initialized_ = true;
    }
    // First-order high-pass at approximately 164Hz, state in ADC counts * 256.
    filteredQ8_ = (static_cast<int32_t>(raw) - previous_) * 256 + filteredQ8_ * 240 / 256;
    previous_ = raw;
    signal_[cursor_] = filteredQ8_ / 16; // counts * 16 preserves small FFT bins
    raw_[cursor_] = raw;
    cursor_ = (cursor_ + 1) % size;
    ++samples_;
    if (filled_ < size) ++filled_;
    ++sinceFrame_;
    if (filled_ != size || sinceFrame_ < hop) return false;
    sinceFrame_ = 0;
    analyze();
    return true;
  }

  const AudioFeatures &features() const { return features_; }
  uint32_t timeMs() const { return static_cast<uint32_t>(samples_ / (sampleRate / 1000)); }

 private:
  static constexpr float pi = 3.14159265358979323846f;

  uint32_t log2Q8(uint64_t value) const {
    if (!value) value = 1;
    const unsigned exponent = 63u - static_cast<unsigned>(__builtin_clzll(value));
    const unsigned index = static_cast<unsigned>(exponent >= 8 ? value >> (exponent - 8) : value << (8 - exponent)) & 255u;
    return exponent * 256 + logFraction_[index];
  }

  void analyze() {
    features_ = {};
    features_.rawMin = 4095;
    unsigned clipped = 0;
    uint64_t sumSquares = 0;
    uint32_t peak = 0, rawSum = 0;
    for (size_t i = 0; i < size; ++i) {
      const size_t source = (cursor_ + i) % size;
      const int32_t value = signal_[source];
      sumSquares += static_cast<int64_t>(value) * value;
      const uint32_t magnitude = value < 0 ? -value : value;
      if (magnitude > peak) peak = magnitude;
      rawSum += raw_[source];
      if (raw_[source] < features_.rawMin) features_.rawMin = raw_[source];
      if (raw_[source] > features_.rawMax) features_.rawMax = raw_[source];
      if (raw_[source] <= 8 || raw_[source] >= 4087) ++clipped;
      real_[i] = static_cast<int64_t>(value) * window_[i] / 32768;
      imag_[i] = 0;
    }
    features_.rms = std::sqrt(static_cast<float>(sumSquares) / (size * 256));
    features_.peak = peak / 16.0f;
    features_.rawMean = static_cast<float>(rawSum) / size;
    features_.clippedFraction = static_cast<float>(clipped) / size;

    for (size_t i = 1, j = 0; i < size; ++i) {
      size_t bit = size >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) {
        const int32_t r = real_[i], im = imag_[i];
        real_[i] = real_[j]; imag_[i] = imag_[j];
        real_[j] = r; imag_[j] = im;
      }
    }
    // Forward radix-2 FFT, normalized by N using one divide-by-two per stage.
    // 64-bit products avoid signed overflow at full ADC input range.
    for (size_t length = 2; length <= size; length <<= 1) {
      for (size_t base = 0; base < size; base += length) {
        for (size_t j = 0; j < length / 2; ++j) {
          const size_t a = base + j, b = a + length / 2;
          const size_t w = j * size / length;
          const int32_t bR = (static_cast<int64_t>(real_[b]) * twiddleR_[w]
                             - static_cast<int64_t>(imag_[b]) * twiddleI_[w]) / 32768;
          const int32_t bI = (static_cast<int64_t>(real_[b]) * twiddleI_[w]
                             + static_cast<int64_t>(imag_[b]) * twiddleR_[w]) / 32768;
          real_[b] = (real_[a] - bR) / 2; imag_[b] = (imag_[a] - bI) / 2;
          real_[a] = (real_[a] + bR) / 2; imag_[a] = (imag_[a] + bI) / 2;
        }
      }
    }
    constexpr size_t firstBin = 4, highBin = 24, lastBin = 120;
    uint64_t total = 0, high = 0, maxTriplet = 0;
    uint32_t logSum = 0;
    // Use a dedicated power array: real/imag still contain FFT components.
    for (size_t i = firstBin; i <= lastBin; ++i) {
      power_[i] = static_cast<int64_t>(real_[i]) * real_[i]
                  + static_cast<int64_t>(imag_[i]) * imag_[i];
      total += power_[i];
      if (i >= highBin) high += power_[i];
      logSum += log2Q8(power_[i]);
    }
    for (size_t i = firstBin; i <= lastBin; ++i) {
      const uint64_t triplet = power_[i] + (i > firstBin ? power_[i - 1] : 0)
          + (i < lastBin ? power_[i + 1] : 0);
      if (triplet > maxTriplet) maxTriplet = triplet;
    }
    if (total) {
      for (size_t i = firstBin; i <= lastBin; ++i) {
        if (power_[i] * 128 >= total) ++features_.occupiedBins;
      }
      constexpr float bins = lastBin - firstBin + 1;
      features_.highRatio = static_cast<float>(high) / total;
      features_.concentration = static_cast<float>(maxTriplet) / total;
      // One exponential per frame; the 117 logarithms use the integer LUT.
      features_.flatness = std::exp2(logSum / (bins * 256)) / (total / bins);
      if (features_.flatness > 1) features_.flatness = 1;
    }
  }

  int16_t window_[size]{}, twiddleR_[size / 2]{}, twiddleI_[size / 2]{};
  uint16_t logFraction_[size]{};
  int32_t signal_[size]{}, real_[size]{}, imag_[size]{};
  uint16_t raw_[size]{};
  uint64_t power_[size / 2 + 1]{};
  size_t cursor_ = 0, filled_ = 0, sinceFrame_ = 0;
  uint64_t samples_ = 0;
  bool initialized_ = false;
  int32_t previous_ = 0, filteredQ8_ = 0;
  AudioFeatures features_;
};
