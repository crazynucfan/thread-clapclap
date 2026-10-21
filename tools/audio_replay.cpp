#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <string>
#include "../firmware/ThreadClapClap/AudioClapDetector.h"
#include "../firmware/ThreadClapClap/ImpulseClapDetector.h"
#include "../firmware/ThreadClapClap/Config.h"

static float score(const AudioClapDetector &) { return -1; }
static float score(const ImpulseClapDetector &d) { return d.candidateScore(); }
template<class Detector> int replay(Detector detector) {
  uint32_t now=0; unsigned bins=0, fluxBins=0, frames=0, clicks=0;
  AudioFeatures f;
  while (true) {
    if (!(std::cin >> now)) {
      if (std::cin.eof()) break;
      std::cerr << "Malformed timestamp\n"; return 2;
    }
    if (!(std::cin >> f.rms >> f.peak >> f.highRatio >> f.flatness >> f.concentration
          >> f.clippedFraction >> bins >> f.onsetFlux >> fluxBins)) {
      std::cerr << "Truncated or malformed feature frame\n"; return 2;
    }
    f.occupiedBins=static_cast<uint16_t>(bins); f.fluxBins=static_cast<uint16_t>(fluxBins);
    if (!frames++) detector.reset(now);
    const auto result=detector.process(f,now);
    if (result.doubleClap) ++clicks;
    if (result.decision != Detector::Decision::None || result.doubleClap) {
      std::cout << "{\"type\":\"decision\",\"sample_ms\":" << now
                << ",\"decision\":\"" << Detector::name(result.decision)
                << "\",\"score\":" << std::setprecision(7) << score(detector)
                << ",\"pending\":" << unsigned(detector.pendingClaps())
                << ",\"double_clap\":" << (result.doubleClap?"true":"false") << "}\n";
    }
  }
  if (!std::cin.eof() || !frames) {std::cerr<<"Malformed or empty feature stream\n"; return 2;}
  std::cout << "{\"type\":\"summary\",\"frames\":"<<frames<<",\"clicks\":"<<clicks<<"}\n";
  return 0;
}
int main(int argc,char **argv) {
  const std::string classifier=argc>1?argv[1]:"burst";
  if(classifier=="legacy") return replay(AudioClapDetector{});
  if(classifier!="burst") return 2;
  auto settings = config::audioDetector;
  if(argc>2) {
    char *end=nullptr; settings.minScore=std::strtof(argv[2],&end);
    if(!end || *end || !std::isfinite(settings.minScore) || settings.minScore<0 || settings.minScore>1) return 2;
  }
  return replay(ImpulseClapDetector{settings});
}
