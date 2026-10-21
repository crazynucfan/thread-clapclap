#pragma once

#include "AudioFeatures.h"

class AudioClapDetector {
 public:
  struct Config {
    uint32_t calibrationMs = 2000;
    float minRms = 18;            // ADC counts after the high-pass filter
    float noiseMultiplier = 4;    // 12dB above the adaptive ambient RMS
    float attackRatio = 2.2f;     // sudden onset relative to previous frame/noise
    float minHighRatio = 0.15f;
    float maxConcentration = 0.55f;
    float minFlatness = 0.08f;
    float minCrest = 1.7f;
    float maxCrest = 12;
    float maxClippedFraction = 0.05f;
    float peakReleaseRatio = 0.25f; // ignore reverb once RMS falls 75% from peak
    uint32_t maxBurstMs = 120;
    uint32_t quietMs = 24;
    uint32_t minClapGapMs = 160;
    uint32_t sequenceGapMs = 600;
    float maxPairRmsRatio = 4;
    float maxPairHighDifference = 0.35f;
  };

  enum class Decision { None, Calibrated, Accepted, Restarted, IgnoredWeaker, SlowAttack, Spectrum, Clipping, Duration };
  struct Result {
    Decision decision = Decision::None;
    bool doubleClap = false;
  };

  AudioClapDetector() = default;
  explicit AudioClapDetector(Config config) : config_(config) {}

  void reset(uint32_t now) {
    started_ = now;
    noise_ = 1;
    previousRms_ = 1;
    calibrated_ = false;
    active_ = blocked_ = false;
    count_ = 0;
    haveLastClap_ = false;
    peak_ = {};
    attack_ = 0;
    duration_ = 0;
  }

  Result process(const AudioFeatures &f, uint32_t now) {
    Result result;
    if (!calibrated_) {
      learn(f.rms, 0.05f);
      previousRms_ = f.rms;
      if (now - started_ >= config_.calibrationMs) {
        calibrated_ = true;
        result.decision = Decision::Calibrated;
      }
      return result;
    }
    const float threshold = std::fmax(config_.minRms, noise_ * config_.noiseMultiplier);
    const bool newImpulse = f.rms >= threshold
        && f.rms >= std::fmax(previousRms_, noise_) * config_.attackRatio;

    if (blocked_) {
      if (newImpulse) {
        startBurst(f, now, threshold);
        previousRms_ = f.rms;
        return result;
      }
      // Learn a sustained new background so it cannot hold the detector blocked
      // forever. It must settle below the updated release level before rearming.
      learn(f.rms, 0.03f);
      if (f.rms > std::fmax(config_.minRms * 0.45f, noise_ * 2)) lastLoud_ = now;
      if (now - lastLoud_ >= config_.quietMs) blocked_ = false;
    } else if (!active_) {
      result.doubleClap = completeSequence(now);
      if (haveLastClap_ && now - lastClap_ < config_.minClapGapMs) {
        // Ignore echo tails during the lockout even when their spectra would
        // be rejected. They must not erase the first clap of a valid pair.
        previousRms_ = f.rms;
        return result;
      }
      if (f.rms >= threshold) {
        startBurst(f, now, threshold);
      } else {
        learn(f.rms, f.rms > noise_ ? 0.01f : 0.05f);
      }
    } else {
      if (newImpulse && f.rms >= peak_.rms * 2) {
        // A fresh loud impulse can start inside a weaker tonal/background
        // candidate. Otherwise unrelated sound can mask a genuine clap.
        startBurst(f, now, threshold);
        previousRms_ = f.rms;
        return result;
      }
      if (f.rms >= releaseLevel_) {
        lastLoud_ = now;
        observe(f);
      }
      duration_ = lastLoud_ - onset_;
      // Judge actual above-threshold duration. Waiting for the release quiet
      // period must not turn an otherwise valid burst into a long sound.
      if (duration_ > config_.maxBurstMs) {
        const bool disruptive = broadband_ && fastAttack_;
        result.decision = disruptive ? Decision::Duration : Decision::Spectrum;
        active_ = false;
        blocked_ = disruptive;
        lastLoud_ = now;
        if (disruptive) count_ = 0;
      } else if (now - lastLoud_ >= config_.quietMs) {
        active_ = false;
        if (lastLoud_ - onset_ > config_.maxBurstMs) result.decision = Decision::Duration;
        else if (clipped_) result.decision = Decision::Clipping;
        else if (!fastAttack_) result.decision = Decision::SlowAttack;
        else if (!broadband_) result.decision = Decision::Spectrum;
        else result.decision = accept(onset_);
        if (result.decision == Decision::Duration || result.decision == Decision::Clipping) count_ = 0;
      }
    }
    previousRms_ = f.rms;
    return result;
  }

  float noiseRms() const { return noise_; }
  bool calibrated() const { return calibrated_; }
  uint8_t pendingClaps() const { return count_; }
  const AudioFeatures &lastCandidate() const { return peak_; }
  uint32_t candidateDurationMs() const { return duration_; }
  uint32_t candidateStartMs() const { return onset_; }
  float candidateAttack() const { return attack_; }

  static const char *name(Decision decision) {
    switch (decision) {
      case Decision::Calibrated: return "calibrated";
      case Decision::Accepted: return "accepted";
      case Decision::Restarted: return "restarted_stronger_or_different";
      case Decision::IgnoredWeaker: return "ignored_weaker";
      case Decision::SlowAttack: return "rejected_slow_attack";
      case Decision::Spectrum: return "rejected_spectrum";
      case Decision::Clipping: return "rejected_clipping";
      case Decision::Duration: return "rejected_duration";
      default: return "none";
    }
  }

 private:
  void learn(float rms, float rate) { noise_ = std::fmax(1.0f, noise_ + rate * (rms - noise_)); }

  void startBurst(const AudioFeatures &f, uint32_t now, float threshold) {
    active_ = true;
    blocked_ = false;
    onset_ = lastLoud_ = now;
    releaseLevel_ = threshold * 0.45f;
    attack_ = f.rms / std::fmax(previousRms_, noise_);
    fastAttack_ = attack_ >= config_.attackRatio;
    duration_ = 0;
    broadband_ = clipped_ = false;
    peak_ = {};
    observe(f);
  }

  void observe(const AudioFeatures &f) {
    if (f.clippedFraction > config_.maxClippedFraction) clipped_ = true;
    if (f.rms > peak_.rms) {
      peak_ = f;
      releaseLevel_ = std::fmax(releaseLevel_, peak_.rms * config_.peakReleaseRatio);
      // Judge the loudest frame, not a quiet tail or the switching transient
      // at the end of a tone. Otherwise a tonal burst could look broadband.
      const float crest = f.peak / std::fmax(f.rms, 1e-6f);
      broadband_ = f.highRatio >= config_.minHighRatio && f.concentration <= config_.maxConcentration
          && f.flatness >= config_.minFlatness && crest >= config_.minCrest && crest <= config_.maxCrest;
    }
  }

  Decision accept(uint32_t now) {
    if (haveLastClap_ && now - lastClap_ < config_.minClapGapMs) return Decision::None;
    if (!haveLastClap_ || now - lastClap_ >= config_.sequenceGapMs) count_ = 0;
    if (count_) {
      const float ratio = peak_.rms / std::fmax(firstRms_, 1e-6f);
      if (ratio < 1 / config_.maxPairRmsRatio) return Decision::IgnoredWeaker;
      if (ratio > config_.maxPairRmsRatio
          || std::fabs(peak_.highRatio - firstHigh_) > config_.maxPairHighDifference) {
        // Resynchronise on the current strong candidate instead of throwing
        // away both it and a preceding weak/unrelated noise candidate.
        firstRms_ = peak_.rms;
        firstHigh_ = peak_.highRatio;
        count_ = 1;
        lastClap_ = now;
        haveLastClap_ = true;
        return Decision::Restarted;
      }
    }
    if (count_ == 0) {
      firstRms_ = peak_.rms;
      firstHigh_ = peak_.highRatio;
    }
    lastClap_ = now;
    haveLastClap_ = true;
    if (count_ < 3) ++count_;
    return Decision::Accepted;
  }

  bool completeSequence(uint32_t now) {
    if (count_ && now - lastClap_ >= config_.sequenceGapMs) {
      const bool matched = count_ == 2;
      count_ = 0;
      return matched;
    }
    return false;
  }

  Config config_;
  AudioFeatures peak_;
  float noise_ = 1, previousRms_ = 1, releaseLevel_ = 0;
  float firstRms_ = 0, firstHigh_ = 0, attack_ = 0;
  uint32_t duration_ = 0;
  uint32_t started_ = 0, onset_ = 0, lastLoud_ = 0, lastClap_ = 0;
  uint8_t count_ = 0;
  bool calibrated_ = false, active_ = false, blocked_ = false;
  bool fastAttack_ = false, broadband_ = false, clipped_ = false, haveLastClap_ = false;
};
