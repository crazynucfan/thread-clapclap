#pragma once

#include "AudioFeatures.h"

// Scores an attack window and the burst envelope. Spectral colour contributes
// evidence; a single low-flatness frame is not an unconditional veto.
class ImpulseClapDetector {
 public:
  struct Config {
    uint32_t calibrationMs = 2000;
    float minRms = 18, noiseMultiplier = 4;
    float minAttack = 2.2f;
    uint32_t attackWindowMs = 32, maxRiseMs = 40;
    float minScore = 0.70f, minSpectralScore = 0.25f;
    float minCrest = 1.7f, maxCrest = 12;
    float maxClippedFraction = 0.05f, peakReleaseRatio = 0.25f;
    uint32_t maxBurstMs = 120, quietMs = 24;
    uint32_t minClapGapMs = 160, sequenceGapMs = 600;
    float maxPairRmsRatio = 4, maxPairHighDifference = 0.35f;
  };
  enum class Decision {
    None, Calibrated, Accepted, Restarted, IgnoredWeaker,
    SlowAttack, Spectrum, Confidence, Crest, Clipping, Duration
  };
  struct Result { Decision decision = Decision::None; bool doubleClap = false; };
  ImpulseClapDetector() = default;
  explicit ImpulseClapDetector(Config config) : config_(config) {}

  void reset(uint32_t now) {
    started_ = now;
    noise_ = previousRms_ = 1;
    calibrated_ = active_ = blocked_ = haveLastClap_ = false;
    count_ = 0;
    peak_ = {};
    score_ = attack_ = spectral_ = flux_ = frontRatio_ = 0;
    duration_ = riseMs_ = 0;
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
    const bool impulse = f.rms >= threshold
        && f.rms >= std::fmax(previousRms_, noise_) * config_.minAttack;
    if (blocked_) {
      if (impulse) start(f, now, threshold);
      else {
        learn(f.rms, 0.03f);
        if (f.rms > std::fmax(config_.minRms * 0.45f, noise_ * 2)) lastLoud_ = now;
        if (now - lastLoud_ >= config_.quietMs) blocked_ = false;
      }
    } else if (!active_) {
      result.doubleClap = complete(now);
      if (!haveLastClap_ || now - lastClap_ >= config_.minClapGapMs) {
        if (f.rms >= threshold) start(f, now, threshold);
        else learn(f.rms, f.rms > noise_ ? 0.01f : 0.05f);
      }
    } else if (impulse && now - onset_ > config_.attackWindowMs && f.rms >= peak_.rms * 2) {
      start(f, now, threshold);
    } else {
      if (f.rms >= release_) {
        lastLoud_ = now;
        observe(f, now);
      }
      duration_ = lastLoud_ - onset_;
      if (duration_ > config_.maxBurstMs) {
        // Persistent tonal backgrounds do not erase a preceding good pair.
        const bool disruptive = spectral_ >= config_.minSpectralScore && attack_ >= config_.minAttack;
        result.decision = disruptive ? Decision::Duration : Decision::Spectrum;
        active_ = false;
        blocked_ = disruptive;
        lastLoud_ = now;
        if (disruptive) count_ = 0;
      } else if (now - lastLoud_ >= config_.quietMs) {
        active_ = false;
        frontRatio_ = energy_ ? frontEnergy_ / energy_ : 0;
        score_ = 0.20f * unit((peak_.rms / onsetNoise_ - 4) / 12)
            + 0.20f * unit((attack_ - 1.5f) / 6.5f)
            + 0.20f * unit((flux_ - 0.25f) / 0.55f)
            + 0.25f * spectral_ + 0.15f * frontRatio_;
        const float crest = peak_.peak / std::fmax(peak_.rms, 1e-6f);
        if (clipped_) result.decision = Decision::Clipping;
        else if (attack_ < config_.minAttack || riseMs_ > config_.maxRiseMs) result.decision = Decision::SlowAttack;
        else if (crest < config_.minCrest || crest > config_.maxCrest) result.decision = Decision::Crest;
        else if (spectral_ < config_.minSpectralScore) result.decision = Decision::Spectrum;
        else if (score_ < config_.minScore) result.decision = Decision::Confidence;
        else result.decision = accept(onset_);
        if (result.decision == Decision::Clipping) count_ = 0;
      }
    }
    previousRms_ = f.rms;
    return result;
  }

  float noiseRms() const { return noise_; }
  bool calibrated() const { return calibrated_; }
  uint8_t pendingClaps() const { return count_; }
  const AudioFeatures &lastCandidate() const { return peak_; }
  uint32_t candidateStartMs() const { return onset_; }
  uint32_t candidateDurationMs() const { return duration_; }
  uint32_t candidateRiseMs() const { return riseMs_; }
  float candidateAttack() const { return attack_; }
  float candidateScore() const { return score_; }
  float candidateFlux() const { return flux_; }
  float candidateSpectralScore() const { return spectral_; }
  float candidateFrontRatio() const { return frontRatio_; }
  static const char *name(Decision d) {
    switch (d) {
      case Decision::Calibrated: return "calibrated";
      case Decision::Accepted: return "accepted";
      case Decision::Restarted: return "restarted_stronger_or_different";
      case Decision::IgnoredWeaker: return "ignored_weaker";
      case Decision::SlowAttack: return "rejected_slow_attack";
      case Decision::Spectrum: return "rejected_spectrum";
      case Decision::Confidence: return "rejected_confidence";
      case Decision::Crest: return "rejected_crest";
      case Decision::Clipping: return "rejected_clipping";
      case Decision::Duration: return "rejected_duration";
      default: return "none";
    }
  }
 private:
  static float unit(float x) { return std::fmax(0.0f, std::fmin(1.0f, x)); }
  void learn(float rms, float rate) { noise_ = std::fmax(1.0f, noise_ + rate * (rms - noise_)); }
  void start(const AudioFeatures &f, uint32_t now, float threshold) {
    active_ = true; blocked_ = clipped_ = false;
    onset_ = lastLoud_ = now;
    onsetNoise_ = noise_;
    release_ = threshold * 0.45f;
    peak_ = {};
    score_ = spectral_ = flux_ = energy_ = frontEnergy_ = frontRatio_ = 0;
    attack_ = f.rms / std::fmax(previousRms_, onsetNoise_);
    duration_ = riseMs_ = 0;
    observe(f, now);
  }
  void observe(const AudioFeatures &f, uint32_t now) {
    clipped_ |= f.clippedFraction > config_.maxClippedFraction;
    if (f.rms > peak_.rms) {
      // Keep evidence proportional to the final peak: a weak broadband
      // precursor cannot lend a loud tonal burst its spectrum or flux.
      const float relative = peak_.rms / f.rms;
      spectral_ *= relative;
      flux_ *= relative;
      peak_ = f;
      riseMs_ = now - onset_;
      release_ = std::fmax(release_, peak_.rms * config_.peakReleaseRatio);
    }
    const float energy = std::fmax(0.0f, f.rms * f.rms - onsetNoise_ * onsetNoise_);
    energy_ += energy;
    if (now - onset_ <= config_.attackWindowMs) {
      frontEnergy_ += energy;
      attack_ = std::fmax(attack_, f.rms / std::fmax(previousRms_, onsetNoise_));
      flux_ = std::fmax(flux_, f.onsetFlux * unit(f.rms / peak_.rms));
      // Evaluate complete attack frames, not later ringing or release transients.
      // Weak frames cannot provide spectral evidence for a much louder peak.
      if (f.rms >= std::fmax(config_.minRms, peak_.rms * 0.25f)) {
        const float bandwidth = 0.5f * unit((f.occupiedBins - 3.0f) / 17)
            + 0.5f * unit((f.fluxBins - 3.0f) / 17);
        const float spread = unit(1 - f.concentration);
        const float evidence = 0.35f * bandwidth + 0.25f * unit(f.flatness / 0.10f)
            + 0.25f * spread + 0.15f * unit(f.highRatio / 0.25f);
        spectral_ = std::fmax(spectral_, evidence * unit(f.rms / peak_.rms));
      }
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
        firstRms_ = peak_.rms; firstHigh_ = peak_.highRatio;
        lastClap_ = now; count_ = 1; haveLastClap_ = true;
        return Decision::Restarted;
      }
    }
    if (!count_) { firstRms_ = peak_.rms; firstHigh_ = peak_.highRatio; }
    lastClap_ = now; haveLastClap_ = true;
    if (count_ < 3) ++count_;
    return Decision::Accepted;
  }
  bool complete(uint32_t now) {
    if (!count_ || now - lastClap_ < config_.sequenceGapMs) return false;
    const bool matched = count_ == 2;
    count_ = 0;
    return matched;
  }
  Config config_;
  AudioFeatures peak_;
  float noise_ = 1, previousRms_ = 1, onsetNoise_ = 1, release_ = 0;
  float score_ = 0, attack_ = 0, spectral_ = 0, flux_ = 0;
  float energy_ = 0, frontEnergy_ = 0, frontRatio_ = 0, firstRms_ = 0, firstHigh_ = 0;
  uint32_t started_ = 0, onset_ = 0, lastLoud_ = 0, duration_ = 0, riseMs_ = 0, lastClap_ = 0;
  uint8_t count_ = 0;
  bool calibrated_ = false, active_ = false, blocked_ = false, clipped_ = false, haveLastClap_ = false;
};
