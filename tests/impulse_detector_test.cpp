#include <cmath>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include "../firmware/ThreadClapClap/ImpulseClapDetector.h"
#include "../firmware/ThreadClapClap/AudioCapture.h"
#include "audio_rig.h"
using Rig = AudioRig<ImpulseClapDetector>;
static void require(bool ok, const char *message) { if (!ok) throw std::runtime_error(message); }

static void validPairs() {
  for (unsigned offset : {0u, 2u, 5u, 9u, 13u}) {
    Rig r; r.warm(); r.quiet(offset); r.clap(); r.quiet(276); r.clap(); r.quiet(900);
    require(r.events == 1, "sharp broadband pair must pass across window alignments");
  }
}
static void singlesTriplesEchoes() {
  Rig r; r.warm(); r.clap(); r.quiet(900);
  r.clap(); r.quiet(276); r.clap(); r.quiet(276); r.clap(); r.quiet(900);
  r.clap(); r.quiet(56); r.clap(); r.quiet(900);
  require(r.events == 0, "single, triple, and echo must not trigger");
}
static void tonesAndRumble() {
  for (float hz : {125.f, 1000.f, 3000.f}) {
    Rig r; r.warm(); r.tone(hz); r.quiet(276); r.tone(hz); r.quiet(900);
    require(r.events == 0, "tonal pairs must not trigger");
  }
}
static void clippingSpikes() {
  Rig r; r.warm(); r.clap(12000); r.quiet(276); r.clap(12000); r.quiet(900);
  r.sample(1500); r.quiet(300); r.sample(1500); r.quiet(900);
  require(r.events == 0, "clipping and isolated sample spikes must remain rejected");
}
static void noiseRecovery() {
  Rig r; r.warm(); r.quiet(1500, 600); r.quiet(1200);
  require(r.events == 0, "sustained noise must not create a pair");
  r.clap(); r.quiet(276); r.clap(); r.quiet(900);
  require(r.events == 1, "must recover after sustained noise");
}
static void slowAttack() {
  Rig r; r.warm();
  for (unsigned rep=0; rep<2; ++rep) {
    for (unsigned i=0;i<6400;++i) r.sample(r.noise()*(3+i*0.08f));
    r.quiet(300);
  }
  r.quiet(900); require(r.events == 0, "slowly rising sound must not pass");
}
static void colouredClaps() {
  Rig r; r.warm();
  for (unsigned rep=0;rep<2;++rep) {
    float low=0;
    for (unsigned i=0;i<384;++i) {
      low += 0.25f*(r.noise()-low);
      r.sample(low * 1800 * std::exp(-static_cast<float>(i)/90.f));
    }
    r.quiet(rep ? 900 : 276);
  }
  require(r.events == 1, "frequency-coloured impulses must pass without a flatness veto");
}
static void reverb() {
  Rig r; r.warm();
  for(unsigned i=0;i<20800;++i) {
    float amplitude=3;
    for(unsigned onset:{0u,4800u}) if(i>=onset) {
      float age=static_cast<float>(i-onset);
      amplitude += 1400*std::exp(-age/90) + 250*std::exp(-age/1280);
    }
    r.sample(r.noise()*amplitude);
  }
  require(r.events == 1, "reverberation must not extend a sharp pair into a long burst");
}
static void fluxMeasuresAttack() {
  Rig r; r.quiet(200);
  float onset=0; unsigned bins=0;
  for(unsigned i=0;i<384;++i) {
    r.sample(r.noise()*1000*std::exp(-static_cast<float>(i)/90.f));
    onset=std::fmax(onset,r.analyzer.features().onsetFlux);
    bins=std::max(bins,static_cast<unsigned>(r.analyzer.features().fluxBins));
  }
  require(onset>0.7f && bins>10, "broadband attack must produce positive multi-bin flux");
  r.tone(1000,200);
  require(r.analyzer.features().onsetFlux<0.05f, "steady tone must have low positive flux");
}
static void resetDiscards() {
  Rig r; r.warm(); r.clap(); r.quiet(150); r.detector.reset(r.analyzer.timeMs());
  r.clap(); r.quiet(2500); require(r.events==0,"reset must discard partial gestures");
}
static void packetLayoutAndCrc() {
  require(captureCrc32("123456789",9)==0xcbf43926u,"CRC32 must match the standard check vector");
  AudioFeatures f; f.rms=482.8f; f.onsetFlux=0.9f; f.fluxBins=23;
  auto p=captureFrame(f,42,3,1234,5678);
  require(p.rms==f.rms && p.flux==f.onsetFlux && p.fluxBins==23,"packet must retain features");
  require(p.checksum==captureCrc32(&p,52),"packet CRC must cover all payload bytes");
}
struct Pulse { uint32_t start, duration; float rms; bool tone = false; bool decay = true; };
static unsigned timeline(std::initializer_list<Pulse> pulses, uint32_t origin = 0) {
  ImpulseClapDetector d; d.reset(origin); unsigned events=0;
  for(uint32_t t=0;t<4500;t+=8) {
    AudioFeatures f; f.rms=7;
    for(const auto &p:pulses) if(t>=p.start && t-p.start<p.duration) {
      const auto age=t-p.start;
      f.rms=p.rms*(p.decay?std::exp(-static_cast<float>(age)/28):1);
      f.peak=f.rms*(p.tone?1.5f:3.f);
      f.highRatio=p.tone?.02f:.25f; f.flatness=p.tone?.01f:.06f;
      f.concentration=p.tone?.95f:.3f; f.occupiedBins=p.tone?3:24;
      f.onsetFlux=age==0?.95f:0;f.fluxBins=age==0 && !p.tone?24:0;
    }
    if(d.process(f,origin+t).doubleClap)++events;
  }
  return events;
}
static void rollover() {
  require(timeline({{2200,80,600},{2504,80,600}},UINT32_MAX-1000)==1,
          "new classifier must handle sample-clock rollover");
}
static void weakPrefixAndThird() {
  require(timeline({{2200,40,70},{2504,80,650},{2808,80,600}})==1,
          "weak precursor must not consume a stronger genuine pair");
  require(timeline({{2200,80,650},{2504,80,600},{2808,40,70}})==1,
          "weak third interruption must not cancel a genuine pair");
}
static void tonalAndBroadbandInterruption() {
  require(timeline({{2200,80,650},{2504,80,600},{2704,240,70,true,false}})==1,
          "long tone must preserve the pending pair");
  require(timeline({{2200,80,650},{2504,80,600},{2704,240,400,false,false}})==0,
          "sustained broadband interruption must cancel the pending pair");
}
static void mismatchedAndDistant() {
  require(timeline({{2200,80,1000},{2504,80,100}})==0,
          "wildly different bursts must not form a pair");
  require(timeline({{2200,80,600},{3000,80,600}})==0,
          "bursts beyond the pairing window must not combine");
}
static void latePeakIsNotSharpAttack() {
  ImpulseClapDetector d;d.reset(0);unsigned events=0;
  for(uint32_t t=0;t<4000;t+=8) {
    AudioFeatures f;f.rms=7;
    for(uint32_t onset:{2200u,2504u})if(t>=onset && t-onset<96) {
      const float age=static_cast<float>(t-onset);
      f.rms=50+age*8;f.peak=f.rms*3;f.highRatio=.5f;f.flatness=.3f;
      f.concentration=.2f;f.occupiedBins=30;f.onsetFlux=.9f;f.fluxBins=25;
    }
    if(d.process(f,t).doubleClap)++events;
  }
  require(events==0,"spectrally broad slow peaks must not gain a clap classification");
}
static void attackEvidenceMustHaveEnoughEnergy() {
  for(float attackRms:{30.f,250.f}) {
    ImpulseClapDetector d;d.reset(0);unsigned events=0;
    for(uint32_t t=0;t<4000;t+=8) {
      AudioFeatures f;f.rms=7;
      for(uint32_t onset:{2200u,2504u})if(t>=onset && t-onset<40) {
        const auto age=t-onset;
        if(age==0) {
          f.rms=attackRms;f.peak=f.rms*3;f.highRatio=.5f;f.flatness=.3f;
          f.concentration=.15f;f.occupiedBins=f.fluxBins=30;f.onsetFlux=.9f;
        } else {
          f.rms=500*std::exp(-static_cast<float>(age-8)/20);f.peak=f.rms*3;
          f.highRatio=.02f;f.flatness=.01f;f.concentration=.95f;f.occupiedBins=3;f.onsetFlux=.1f;
        }
      }
      if(d.process(f,t).doubleClap)++events;
    }
    require(events==(attackRms>100?1u:0u),
            "strong broad attacks may precede ringing; weak precursors must not lend their spectrum");
  }
}
int main() {
  const struct {const char *name; void(*run)();} tests[]={
    {"burst score valid pairs",validPairs},{"burst score single triple echoes",singlesTriplesEchoes},
    {"burst score tones and rumble",tonesAndRumble},{"burst score clipping and spikes",clippingSpikes},
    {"burst score noise recovery",noiseRecovery},{"burst score slow attacks",slowAttack},
    {"burst score coloured claps",colouredClaps},{"burst score reverberation",reverb},
    {"multi-bin onset flux",fluxMeasuresAttack},{"burst reset",resetDiscards},{"capture CRC and layout",packetLayoutAndCrc},
    {"burst sample-clock rollover",rollover},{"burst weak precursor and third",weakPrefixAndThird},
    {"burst tonal and broadband interruptions",tonalAndBroadbandInterruption},
    {"burst mismatched and distant pairs",mismatchedAndDistant},
    {"burst rejects a late peak",latePeakIsNotSharpAttack},
    {"burst evidence weighted by amplitude",attackEvidenceMustHaveEnoughEnergy}};
  unsigned failures=0;
  for(const auto &t:tests)try{t.run();std::cout<<"PASS "<<t.name<<'\n';}
    catch(const std::exception &e){++failures;std::cerr<<"FAIL "<<t.name<<": "<<e.what()<<'\n';}
  return failures?EXIT_FAILURE:EXIT_SUCCESS;
}
