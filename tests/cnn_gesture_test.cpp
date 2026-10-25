#include <cassert>
#include <cstdio>
#include <initializer_list>
#include "../firmware/ThreadClapClap/CnnGesture.h"

static unsigned replay(const uint32_t *times,unsigned n) {
  CnnGesture rule(.8f,CnnGesture::Policy::ExactTwo);unsigned clicks=0;
  for (unsigned i=0;i<n;++i) {
    rule.candidate(times[i]*8,.9f,true,1);
    clicks+=rule.advance(times[i]*8);
  }
  clicks+=rule.advance((times[n-1]+1000ull)*8);return clicks;
}
int main() {
  const uint32_t pair[]={2000,2300},single[]={2000},triple[]={2000,2300,2600},echo[]={2000,2100},boundary[]={2000,2600};
  assert(replay(pair,2)==1);assert(replay(single,1)==0);assert(replay(triple,3)==0);
  assert(replay(echo,2)==0);assert(replay(boundary,2)==0);
  std::puts("PASS CNN onset pair timing, single/triple/echo rejection");
  CnnGesture rule(.8f,CnnGesture::Policy::ExactTwo);
  rule.candidate(2580*8,.99f,true,.1);rule.candidate(2814*8,.99f,true,.01);
  rule.candidate(2876*8,.99f,true,.2);rule.candidate(2924*8,.98f,true,.02);rule.candidate(2976*8,.95f,true,.01);
  assert(rule.advance(4000*8));assert(!rule.advance(5000*8));
  std::puts("PASS CNN association uses strongest attack rather than early equal-score window");
  rule.reset();rule.candidate(2000*8,.9f,true,1);rule.candidate(2300*8,.9f,true,1);
  assert(!rule.advance(2500*8));
  rule.candidate(2890*8,.9f,true,1); // Third classification arrives after the second clap's wall-clock timeout.
  assert(!rule.advance(3000*8));assert(!rule.advance(4000*8));
  std::puts("PASS CNN classifier watermark prevents premature action before delayed third clap");
  rule.reset();rule.candidate(2000*8,.9f,true,1);rule.candidate(2300*8,.9f,true,1);rule.reset();
  assert(!rule.advance(4000*8));
  std::puts("PASS CNN reset discards incomplete gestures");
  for (const uint32_t gap:{160u,200u,250u,300u,599u}) {
    CnnGesture fast(.8f);
    fast.candidate(2000*8,.9f,true,1);
    assert(!fast.advance(2120*8));
    fast.candidate((2000+gap)*8,.9f,true,1);
    assert(!fast.advance((2000+gap+119)*8));
    assert(fast.advance((2000+gap+120)*8));
    assert(!fast.advance((2000+gap+1000)*8));
  }
  std::puts("PASS immediate CNN pairs at 160/200/250/300/599ms fire after association without quiet wait");
  for (const uint32_t gap:{159u,600u}) {
    CnnGesture fast(.8f);fast.candidate(2000*8,.9f,true,1);fast.candidate((2000+gap)*8,.9f,true,1);
    assert(!fast.advance((3000+gap)*8));
  }
  std::puts("PASS immediate CNN minimum/maximum gap boundaries");
  {
    CnnGesture fast(.8f);unsigned actions=0;
    for (const uint32_t now:{2000u,2200u,2400u,2600u,4000u,4250u}) {
      fast.candidate(now*8,.9f,true,1);actions+=fast.advance((now+120)*8);
    }
    actions+=fast.advance(6000*8);assert(actions==2);
  }
  std::puts("PASS immediate CNN longer clap bursts produce one action and rearm after quiet");
  {
    CnnGesture fast(.8f);fast.candidate(2000*8,.9f,true,1);assert(!fast.advance(2120*8));
    fast.candidate(2250*8,.9f,true,1);fast.reset();assert(!fast.advance(4000*8));
    fast.candidate(5000*8,.1f,true,1);fast.candidate(5250*8,.9f,false,1);assert(!fast.advance(7000*8));
  }
  std::puts("PASS immediate CNN reset and rejected candidates cannot emit an action");

}
