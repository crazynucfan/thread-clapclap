// Optional numeric parity harness. Writes private event features to stdout.
#include <cstdio>
#include <cstdint>
#include "../firmware/ThreadClapClap/CnnFrontend.h"
int main() {
  static CnnStream stream;static CnnFeatures frontend;
  static float raw[2048];static int8_t feature[480];
  stream.reset();uint16_t adc;
  while (std::fread(&adc,sizeof(adc),1,stdin)==1) {
    stream.push(adc);CnnStream::Candidate c;
    if (stream.pop(raw,c)) {
      frontend.extract(raw,feature);
      std::fwrite(&c.onset,sizeof(c.onset),1,stdout);std::fwrite(&c.strength,sizeof(c.strength),1,stdout);
      std::fwrite(feature,sizeof(feature),1,stdout);
    }
    if (stream.fault()) return 2;
  }
  return std::ferror(stdin)||std::ferror(stdout);
}
