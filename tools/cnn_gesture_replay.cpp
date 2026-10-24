#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include "../firmware/ThreadClapClap/CnnGesture.h"
int main(int argc,char **argv) {
  if(argc!=2)return 2;
  CnnGesture rule(std::strtof(argv[1],nullptr));
  unsigned actions=0;uint64_t onset=0,last=0;float score;uint8_t top;double strength;
  while(std::fread(&onset,sizeof(onset),1,stdin)==1) {
    if(std::fread(&score,sizeof(score),1,stdin)!=1 || std::fread(&top,sizeof(top),1,stdin)!=1
        || std::fread(&strength,sizeof(strength),1,stdin)!=1) return 2;
    rule.candidate(onset,score,top!=0,strength);actions+=rule.advance(onset);last=onset;
  }
  actions+=rule.advance(last+10000);std::printf("%u\n",actions);return 0;
}
