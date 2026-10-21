#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

#include "../firmware/ThreadClapClap/ClapDetector.h"

static void require(bool condition, const char *message) {
  if (!condition) throw std::runtime_error(message);
}

struct Rig {
  ClapDetector detector;
  uint32_t origin;
  unsigned events = 0;

  explicit Rig(uint32_t start = 0) : origin(start) {
    detector.reset(origin, false);
    level(40, false);
  }
  void level(uint32_t time, bool active) {
    if (detector.update(origin + time, active)) ++events;
  }
  void clap(uint32_t time, uint32_t width = 5) {
    level(time, true);
    level(time + width, false);
    level(time + width + 40, false);
  }
  void finish(uint32_t time = 2000) { level(time, false); }
};

static void silenceAndSingleClap() {
  Rig r;
  r.finish();
  r.clap(2200);
  r.finish(3000);
  require(r.events == 0, "silence and a single clap must not trigger");
}

static void doubleClap() {
  Rig r;
  r.clap(100);
  r.clap(400);
  r.level(999, false);
  require(r.events == 0, "wait for the complete sequence");
  r.level(1000, false);
  r.finish();
  require(r.events == 1, "two claps must emit exactly once");
}

static void tripleAndLongerSequences() {
  for (unsigned count : {3u, 4u, 12u}) {
    Rig r;
    for (unsigned i = 0; i < count; ++i) r.clap(100 + i * 200);
    r.finish(100 + count * 200 + 600);
    require(r.events == 0, "three or more claps must not trigger");
  }
}

static void echoes() {
  Rig r;
  r.clap(100);
  r.clap(180);  // echo within the 160ms lockout
  r.finish();
  require(r.events == 0, "echo must not become a second clap");
  r.clap(2200);
  r.clap(2280);
  r.clap(2500);
  r.finish(3200);
  require(r.events == 1, "echo must not suppress a later genuine second clap");
}

static void comparatorChatter() {
  Rig r;
  for (uint32_t start : {100u, 400u}) {
    for (uint32_t dt = 0; dt < 30; dt += 2) {
      r.level(start + dt, true);
      r.level(start + dt + 1, false);
    }
    r.level(start + 70, false);
  }
  r.finish();
  require(r.events == 1 && r.detector.acceptedClaps() == 2,
          "rapid comparator transitions must form two bursts, not many claps");
}

static void sustainedNoise() {
  Rig r;
  r.clap(100);
  for (uint32_t time = 400; time < 800; time += 2) {
    r.level(time, true);
    r.level(time + 1, false);
  }
  r.level(850, false);
  r.clap(1000);
  r.finish();
  require(r.events == 0, "sustained noise must cancel a pending sequence");
  r.clap(2200);
  r.clap(2500);
  r.finish(3200);
  require(r.events == 1, "detection must recover after noise");
}

static void stuckLow() {
  Rig r;
  r.clap(100);
  r.level(400, true);
  r.level(5000, true);
  require(r.events == 0, "stuck LOW must not complete a double clap");
  r.level(5100, false);
  r.finish(5200);
  require(r.events == 0, "a long pulse must not be accepted on release");
}

static void gapBoundaries() {
  for (uint32_t gap : {159u, 160u, 599u, 600u, 900u}) {
    Rig r;
    r.clap(100);
    r.clap(100 + gap);
    r.finish();
    require(r.events == (gap >= 160 && gap < 600 ? 1u : 0u), "incorrect clap gap boundary");
  }
}

static void rollover() {
  Rig r(UINT32_MAX - 250);
  r.clap(100);
  r.clap(400);
  r.finish();
  require(r.events == 1, "millis rollover must not lose the gesture");
}

static void resetDiscardsPartialSequence() {
  Rig r;
  r.clap(100);
  r.detector.reset(300, false); // queue overflow, reconnect or startup
  r.level(350, false);
  r.clap(400);
  r.finish();
  require(r.events == 0, "reset must discard the partial gesture");
}

static void startupWhileLow() {
  Rig r;
  r.detector.reset(100, true);
  r.level(500, false);
  r.level(550, false);
  r.clap(700);
  r.finish();
  require(r.events == 0, "startup LOW must not count as a clap");
}

static void delayedTaskService() {
  Rig r;
  r.clap(100);
  r.level(400, true);
  r.level(405, false);
  r.level(1100, false); // task delayed; captured pulse was still short
  require(r.events == 1, "task latency must not turn a short pulse into sustained noise");
}

static void twoSeparatePairs() {
  Rig r;
  r.clap(100);
  r.clap(400);
  r.finish(1100);
  r.clap(1500);
  r.clap(1800);
  r.finish(2500);
  require(r.events == 2, "separate pairs must each trigger once");
}

static void noiseAfterPair() {
  Rig r;
  r.clap(100);
  r.clap(400);
  r.level(700, true);
  r.level(950, false);
  r.finish();
  require(r.events == 0, "sustained sound before completion must cancel the pair");
}

int main() {
  const struct { const char *name; void (*run)(); } tests[] = {
    {"silence and single clap", silenceAndSingleClap},
    {"double clap exactly once", doubleClap},
    {"triple and longer sequences", tripleAndLongerSequences},
    {"echo suppression", echoes},
    {"comparator chatter", comparatorChatter},
    {"sustained noise and recovery", sustainedNoise},
    {"stuck LOW", stuckLow},
    {"timing boundaries", gapBoundaries},
    {"millis rollover", rollover},
    {"reset discards partial sequence", resetDiscardsPartialSequence},
    {"startup while LOW", startupWhileLow},
    {"delayed task service", delayedTaskService},
    {"two separate pairs", twoSeparatePairs},
    {"noise after pair", noiseAfterPair},
  };
  unsigned failed = 0;
  for (const auto &test : tests) {
    try {
      test.run();
      std::cout << "PASS " << test.name << '\n';
    } catch (const std::exception &error) {
      ++failed;
      std::cerr << "FAIL " << test.name << ": " << error.what() << '\n';
    }
  }
  return failed ? EXIT_FAILURE : EXIT_SUCCESS;
}
