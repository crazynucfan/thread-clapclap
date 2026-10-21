#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <vector>

#include "../firmware/ThreadClapClap/AudioClapDetector.h"

static void require(bool condition, const char *message) {
  if (!condition) throw std::runtime_error(message);
}

#include "audio_rig.h"
using Rig = AudioRig<AudioClapDetector>;

static void dcAndSilence() {
  AudioAnalyzer a;
  for (unsigned i = 0; i < 4096; ++i) a.push(3000);
  require(a.features().rms == 0, "DC must be removed without a startup impulse");
  require(std::isfinite(a.features().flatness), "silent spectrum must remain finite");
  require(a.features().rawMean == 3000, "raw bias diagnostic must be retained");
  Rig r; r.warm(); r.quiet(3000);
  require(r.events == 0 && r.accepted == 0, "quiet room must not create claps");
}

static void fftTone() {
  Rig r; r.tone(1000, 200);
  const auto f = r.analyzer.features();
  require(f.concentration > 0.95f, "FFT must put a sinusoid in neighbouring bins");
  require(f.highRatio < 0.01f && f.flatness < 0.01f, "tone spectrum must not look broadband");
  require(f.occupiedBins <= 5, "a tone must occupy only a few significant bins");
  require(f.rms > 450 && f.rms < 650, "time-domain RMS must have the expected scale");
}

static void fftBroadband() {
  Rig r; r.quiet(200, 500);
  const auto f = r.analyzer.features();
  require(f.highRatio > 0.5f && f.concentration < 0.3f && f.flatness > 0.2f,
          "white noise must have a broadband FFT signature");
  require(f.occupiedBins >= 20, "broadband noise must occupy many significant bins");
}

static void doubleClapAndPhase() {
  for (unsigned offset : {0u, 2u, 4u, 6u, 9u, 13u}) {
    Rig r; r.warm(); r.quiet(offset);
    r.clap(); r.quiet(276); r.clap(); r.quiet(800);
    if (r.events != 1) {
      std::cerr << "offset=" << offset << " accepted=" << r.accepted << " rejected=" << r.rejected << '\n';
      for (auto d : r.decisions) std::cerr << AudioClapDetector::name(d) << '\n';
    }
    require(r.events == 1, "a broadband pair must trigger once at different FFT alignments");
  }
}

static void singleAndTriple() {
  Rig r; r.warm();
  r.clap(); r.quiet(900);
  require(r.events == 0, "single clap must not trigger");
  r.clap(); r.quiet(276); r.clap(); r.quiet(276); r.clap(); r.quiet(800);
  require(r.events == 0, "triple clap must not trigger");
}

static void tonalPairs() {
  for (float frequency : {125.0f, 1000.0f, 3000.0f}) {
    Rig r; r.warm();
    r.tone(frequency); r.quiet(276); r.tone(frequency); r.quiet(800);
    require(r.events == 0 && r.accepted == 0, "tone or low rumble pairs must be rejected");
  }
}

static void continuousNoiseAndRecovery() {
  Rig r; r.warm();
  r.quiet(1200, 600); r.quiet(1000);
  require(r.events == 0, "sustained broadband noise must not trigger");
  r.clap(); r.quiet(276); r.clap(); r.quiet(800);
  require(r.events == 1, "detector must recover from sustained background sound");
}

static void slowAttack() {
  Rig r; r.warm();
  for (unsigned repeat = 0; repeat < 2; ++repeat) {
    for (unsigned i = 0; i < 400 * 16; ++i) r.sample(r.noise() * (3 + i * 0.08f));
    r.quiet(300);
  }
  r.quiet(800);
  require(r.events == 0, "slowly rising sound must not become a double clap");
}

static void clipping() {
  Rig r; r.warm();
  r.clap(12000); r.quiet(276); r.clap(12000); r.quiet(800);
  require(r.events == 0 && r.accepted == 0, "heavily clipped bursts must be rejected");
}

static void electronicSpikes() {
  Rig r; r.warm();
  for (unsigned repeat = 0; repeat < 2; ++repeat) {
    r.sample(1500); r.quiet(300);
  }
  r.quiet(800);
  require(r.events == 0, "isolated electrical sample spikes must not trigger");
}

static void mismatchedPair() {
  Rig r; r.warm();
  r.clap(1800); r.quiet(276); r.clap(200); r.quiet(800);
  require(r.events == 0, "a very different second burst must fail pair consistency");
}

static void echoSuppression() {
  Rig r; r.warm();
  r.clap(); r.quiet(56); r.clap(); r.quiet(800);
  require(r.events == 0, "an echo inside 160ms must not complete a pair");
}

static void resetDiscardsGesture() {
  Rig r; r.warm(); r.clap(); r.quiet(100);
  r.detector.reset(r.analyzer.timeMs());
  r.clap(); r.quiet(2500);
  require(r.events == 0, "stream/reset or reconnect must discard a partial pair");
}

static void millisRollover() {
  AudioClapDetector d;
  const uint32_t origin = UINT32_MAX - 1000;
  d.reset(origin);
  AudioFeatures quiet;
  quiet.rms = 2;
  AudioFeatures clap;
  clap.rms = 100; clap.peak = 350; clap.highRatio = 0.75f;
  clap.flatness = 0.5f; clap.concentration = 0.08f;
  unsigned events = 0;
  for (uint32_t t = 0; t <= 3400; t += 8) {
    const bool active = (t >= 2200 && t < 2224) || (t >= 2504 && t < 2528);
    if (d.process(active ? clap : quiet, origin + t).doubleClap) ++events;
  }
  require(events == 1, "analog sequence must survive millis rollover");
}

static void reverberantPair() {
  Rig r; r.warm();
  for (unsigned i = 0; i < 1300 * 16; ++i) {
    float amplitude = 3;
    for (unsigned onset : {0u, 300u * 16}) {
      if (i >= onset) {
        const float age = static_cast<float>(i - onset);
        amplitude += 1400 * std::exp(-age / 90) + 250 * std::exp(-age / 1280);
      }
    }
    r.sample(r.noise() * amplitude);
  }
  require(r.events == 1, "quiet reverberation tails must not turn a sharp pair into sustained noise");
}

static void measuredClapSpectrumAndNoiseBetween() {
  AudioClapDetector d;
  d.reset(0);
  AudioFeatures quiet;
  quiet.rms = 7;
  AudioFeatures clap;
  // Feature values observed on the real module's first controlled clap test.
  // Replay features only; no raw recording or room-accuracy claim is implied.
  clap.rms = 658; clap.peak = 2000; clap.highRatio = 0.38f;
  clap.flatness = 0.10f; clap.concentration = 0.23f;
  AudioFeatures tone = clap;
  tone.rms = 60; tone.peak = 100; tone.highRatio = 0.02f;
  tone.flatness = 0.02f; tone.concentration = 0.7f;
  unsigned events = 0;
  for (uint32_t now = 0; now <= 3400; now += 8) {
    const bool isClap = (now >= 2200 && now < 2224) || (now >= 2504 && now < 2528);
    const bool isTone = now >= 2384 && now < 2400;
    if (d.process(isClap ? clap : isTone ? tone : quiet, now).doubleClap) ++events;
  }
  require(events == 1, "the module's broadband clap signature must pass while a short tonal interruption is ignored");
}

static void releaseQuietIsNotBurstDuration() {
  AudioClapDetector d;
  d.reset(0);
  AudioFeatures quiet;
  quiet.rms = 7;
  AudioFeatures clap;
  clap.rms = 400; clap.peak = 1400; clap.highRatio = 0.25f;
  clap.flatness = 0.2f; clap.concentration = 0.3f;
  unsigned events = 0;
  for (uint32_t now = 0; now <= 3400; now += 8) {
    const bool active = (now >= 2200 && now < 2320) || (now >= 2504 && now < 2624);
    if (d.process(active ? clap : quiet, now).doubleClap) ++events;
  }
  require(events == 1, "release confirmation time must not be counted as loud burst duration");
}

// Replay measured feature ranges with explicit timing. These are regression
// fixtures for sequence handling, not raw recordings or an accuracy benchmark.
struct FeatureBurst {
  uint32_t start, end;
  float rms;
  bool tonal = false;
  float flatness = 0.14f;
  uint16_t occupiedBins = 26;
  float highRatio = 0.4f;
  float concentration = 0.23f;
  float crest = 3;
};

static unsigned replayFeatures(std::initializer_list<FeatureBurst> bursts, float ambientRms = 7) {
  AudioClapDetector detector;
  detector.reset(0);
  unsigned events = 0;
  for (uint32_t now = 0; now <= 4400; now += 8) {
    AudioFeatures frame;
    frame.rms = ambientRms;
    for (const auto &burst : bursts) {
      if (now < burst.start || now >= burst.end) continue;
      frame.rms = burst.rms;
      frame.peak = burst.rms * (burst.tonal ? 1.5f : burst.crest);
      frame.highRatio = burst.tonal ? 0.02f : burst.highRatio;
      frame.flatness = burst.tonal ? 0.02f : burst.flatness;
      frame.concentration = burst.tonal ? 0.64f : burst.concentration;
      frame.occupiedBins = burst.tonal ? 3 : burst.occupiedBins;
    }
    if (detector.process(frame, now).doubleClap) ++events;
  }
  return events;
}

static void longToneAfterPair() {
  require(replayFeatures({{2200, 2280, 482}, {2504, 2576, 390},
                          {2704, 3000, 73, true}}) == 1,
          "a long narrowband interruption must not erase two genuine claps");
}

static void longBroadbandAfterPair() {
  require(replayFeatures({{2200, 2280, 482}, {2504, 2576, 390},
                          {2704, 3000, 400}}) == 0,
          "sustained broadband sound must still cancel a pending pair");
}

static void weakPrefixBeforePair() {
  require(replayFeatures({{2200, 2224, 71}, {2504, 2584, 674},
                          {2808, 2888, 551}}) == 1,
          "weak unrelated sound before a real pair must not discard its first clap");
}

static void weakThirdAfterPair() {
  require(replayFeatures({{2200, 2280, 624}, {2504, 2584, 771},
                          {2808, 2832, 72}}) == 1,
          "a much weaker interruption must not count as a third clap");
}

static void comparableThirdCancels() {
  require(replayFeatures({{2200, 2280, 624}, {2504, 2584, 771},
                          {2808, 2888, 650}}) == 0,
          "three comparable claps must still cancel the gesture");
}

static void clapInsideWeakTone() {
  require(replayFeatures({{2192, 2296, 60, true}, {2200, 2280, 650},
                          {2504, 2584, 600}}) == 1,
          "a strong clap onset during a weak tone must begin a new candidate");
}

static void measuredUnevenSpectrumPair() {
  // Labelled physical pair: 296ms apart, zero clipping; only the second
  // burst failed the old flatness gate. Preserve the measured feature bounds.
  require(replayFeatures({{2200, 2264, 482.8f, false, 0.16f, 26, 0.21f, 0.29f, 3.04f},
                          {2496, 2544, 425.3f, false, 0.03f, 23, 0.16f, 0.36f, 3.83f}}) == 1,
          "a strong spread-spectrum companion must support the measured pair");
}

static void spreadFirstStrictSecond() {
  require(replayFeatures({{2200, 2248, 425.3f, false, 0.03f, 23, 0.16f, 0.36f, 3.83f},
                          {2496, 2560, 482.8f, false, 0.16f, 26, 0.21f, 0.29f, 3.04f}}) == 1,
          "strict spectral evidence may occur in either clap");
}

static void spreadPairNeedsStrictEvidence() {
  require(replayFeatures({{2200, 2248, 425, false, 0.03f, 23},
                          {2496, 2544, 425, false, 0.03f, 23}}) == 0,
          "two low-flatness candidates without strict evidence must not trigger");
}

static void weakSpreadCannotSupportPair() {
  require(replayFeatures({{2200, 2248, 300},
                          {2496, 2544, 100, false, 0.03f, 23}}) == 0,
          "weak low-flatness sound must not use the spread-spectrum path");
}

static void narrowSpreadCannotSupportPair() {
  require(replayFeatures({{2200, 2248, 425},
                          {2496, 2544, 425, false, 0.03f, 8}}) == 0,
          "a low-flatness sound concentrated in few bins must remain rejected");
}

static void restartCannotCarryStrictEvidence() {
  require(replayFeatures({{2200, 2248, 160},
                          {2496, 2544, 800, false, 0.03f, 23},
                          {2792, 2840, 800, false, 0.03f, 23}}) == 0,
          "strict evidence from a discarded precursor must not authorise a later pair");
}

static void spreadMustClearAmbientNoise() {
  require(replayFeatures({{2200, 2248, 800},
                          {2496, 2544, 250, false, 0.03f, 23}}, 30) == 0,
          "spread-spectrum support must clear the stricter ambient-noise margin");
}

static void expiredSequenceCannotCarryStrictEvidence() {
  require(replayFeatures({{2200, 2248, 425},
                          {3000, 3048, 425, false, 0.03f, 23},
                          {3296, 3344, 425, false, 0.03f, 23}}) == 0,
          "a new pair must not reuse strict evidence from an expired single clap");
}

int main() {
  const struct { const char *name; void (*run)(); } tests[] = {
    {"DC removal and silence", dcAndSilence}, {"FFT sinusoid", fftTone},
    {"FFT broadband noise", fftBroadband}, {"double clap across FFT alignments", doubleClapAndPhase},
    {"single and triple rejection", singleAndTriple}, {"tonal and low-frequency pairs", tonalPairs},
    {"sustained noise and recovery", continuousNoiseAndRecovery}, {"slow attack rejection", slowAttack},
    {"clipping rejection", clipping}, {"electrical spike rejection", electronicSpikes},
    {"pair similarity", mismatchedPair}, {"echo suppression", echoSuppression},
    {"reset discards partial pair", resetDiscardsGesture}, {"millis rollover", millisRollover},
    {"reverberant double clap", reverberantPair},
    {"measured feature bounds and tonal interruption", measuredClapSpectrumAndNoiseBetween},
    {"release quiet excluded from burst duration", releaseQuietIsNotBurstDuration},
    {"long tone after pair", longToneAfterPair},
    {"long broadband sound after pair", longBroadbandAfterPair},
    {"weak prefix before pair", weakPrefixBeforePair},
    {"weak third interruption", weakThirdAfterPair},
    {"comparable third cancels", comparableThirdCancels},
    {"clap onset inside weak tone", clapInsideWeakTone},
    {"measured uneven-spectrum pair", measuredUnevenSpectrumPair},
    {"spread first and strict second", spreadFirstStrictSecond},
    {"spread pair requires strict evidence", spreadPairNeedsStrictEvidence},
    {"weak spread rejection", weakSpreadCannotSupportPair},
    {"narrow spread rejection", narrowSpreadCannotSupportPair},
    {"restart discards strict spectral evidence", restartCannotCarryStrictEvidence},
    {"spread must clear ambient noise", spreadMustClearAmbientNoise},
    {"expired sequence discards strict evidence", expiredSequenceCannotCarryStrictEvidence},
  };
  unsigned failed = 0;
  for (const auto &test : tests) {
    try { test.run(); std::cout << "PASS " << test.name << '\n'; }
    catch (const std::exception &e) { ++failed; std::cerr << "FAIL " << test.name << ": " << e.what() << '\n'; }
  }
  return failed ? EXIT_FAILURE : EXIT_SUCCESS;
}
