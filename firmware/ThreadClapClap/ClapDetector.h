#pragma once

#include <stdint.h>

// Hardware-independent, active-low sound-envelope detector. Times are uint32_t
// milliseconds; subtraction deliberately handles the millis() rollover.
class ClapDetector {
 public:
  struct Config {
    uint32_t quietMs = 40;        // HIGH continuously before a burst ends
    uint32_t maxBurstMs = 120;    // reject sustained sound / a stuck LOW input
    uint32_t minClapGapMs = 160;  // suppress echoes from one physical clap
    uint32_t sequenceGapMs = 600; // silence after the last clap closes a sequence
  };

  ClapDetector() = default;
  explicit ClapDetector(Config config) : config_(config) {}

  // Start/recover with a quiet period. A partial burst can never become a clap.
  void reset(uint32_t now, bool active) {
    active_ = active;
    burst_ = true;
    invalidBurst_ = true;
    burstStart_ = lastEdge_ = now;
    count_ = 0;
    haveLastClap_ = false;
  }

  // Call for every captured level change, then once with the current level/time.
  // Returns true exactly once for a completed sequence of TWO claps. A third
  // clap before the silence timeout cancels that sequence.
  bool update(uint32_t now, bool active) {
    const bool matched = advance(now);
    if (active != active_) {
      active_ = active;
      lastEdge_ = now;
      if (active && !burst_) {
        burst_ = true;
        invalidBurst_ = false;
        burstStart_ = now;
      }
    }
    return matched;
  }

  uint8_t pendingClaps() const { return count_; }
  uint32_t acceptedClaps() const { return acceptedClaps_; }
  uint32_t rejectedBursts() const { return rejectedBursts_; }

 private:
  bool advance(uint32_t now) {
    if (burst_ && now - burstStart_ > config_.maxBurstMs) {
      // Only the signal's duration counts, not how late the task was serviced.
      if (active_ || lastEdge_ - burstStart_ > config_.maxBurstMs) {
        invalidBurst_ = true;
        count_ = 0;
      }
    }
    if (burst_ && !active_ && now - lastEdge_ >= config_.quietMs) {
      if (invalidBurst_) {
        ++rejectedBursts_;
        count_ = 0;
      } else {
        acceptBurst(burstStart_);
      }
      burst_ = false;
    }
    if (!burst_ && count_ && now - lastClap_ >= config_.sequenceGapMs) {
      const bool matched = count_ == 2;
      count_ = 0;
      return matched;
    }
    return false;
  }

  void acceptBurst(uint32_t start) {
    if (haveLastClap_ && start - lastClap_ < config_.minClapGapMs) {
      return;
    }
    if (!haveLastClap_ || start - lastClap_ >= config_.sequenceGapMs) {
      count_ = 0;
    }
    lastClap_ = start;
    haveLastClap_ = true;
    if (count_ < 3) {  // Saturate: any sequence longer than two is rejected.
      ++count_;
    }
    ++acceptedClaps_;
  }

  Config config_;
  bool active_ = false;
  bool burst_ = false;
  bool invalidBurst_ = false;
  bool haveLastClap_ = false;
  uint8_t count_ = 0;
  uint32_t burstStart_ = 0;
  uint32_t lastEdge_ = 0;
  uint32_t lastClap_ = 0;
  uint32_t acceptedClaps_ = 0;
  uint32_t rejectedBursts_ = 0;
};
