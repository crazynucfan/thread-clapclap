#pragma once
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include "CnnTables.local.h"

// Portable reference implementation shared by native parity checks and the C6.
// The centered resampler delays delivery by 20 input samples (1.25ms); onset
// timestamps remain in the original 8kHz sample clock.
class CnnFeatures {
 public:
  void extract(const float *raw, int8_t *output) {
    int64_t sum=0;
    for (unsigned i=0;i<2048;++i) {normalized_[i]=static_cast<int32_t>(raw[i]*16777216.f);sum+=normalized_[i];}
    const int32_t mean=static_cast<int32_t>(sum/2048);
    uint64_t squares=0;
    for (unsigned i=0;i<2048;++i) {
      normalized_[i]-=mean;
      squares+=static_cast<int64_t>(normalized_[i])*normalized_[i];
    }
    // Same RMS target/floor in Q24 units. Integer accumulation avoids thousands
    // of software double operations on the C6, which has no hardware FPU.
    const float gain=1677721.6f/std::max(std::sqrt(static_cast<float>(squares)/2048.f),167.77216f);
    for (auto &value:normalized_) value=static_cast<int32_t>(value*gain);
    for (unsigned frame=0;frame<15;++frame) {
      for (unsigned i=0;i<256;++i) {
        real_[i]=static_cast<int32_t>((static_cast<int64_t>(normalized_[frame*128+i])*cnn_tables::hannQ30[i]+(1ll<<29))>>30); imag_[i]=0;
      }
      fft();
      for (unsigned i=0;i<129;++i) {
        const float r=real_[i]/16777216.f,im=imag_[i]/16777216.f;
        power_[i]=r*r+im*im;
      }
      for (unsigned band=0;band<32;++band) {
        float value=0;
        for (unsigned j=cnn_tables::melOffsets[band];j<cnn_tables::melOffsets[band+1];++j)
          value+=power_[cnn_tables::melBins[j]]*cnn_tables::melWeights[j];
        // Exact analytic boundaries for the selected model's clipped/scaled
        // log-mel INT8 codes; binary search avoids a software log per band.
        unsigned low=0,high=255;
        while (low<high) {
          const unsigned middle=(low+high)/2;
          if (value>=cnn_tables::logQuantizationBoundaries[middle]) low=middle+1;
          else high=middle;
        }
        output[frame*32+band]=static_cast<int8_t>(static_cast<int>(low)-128);
      }
    }
  }
  static void synthetic(float *raw) {
    uint32_t state=0x12345678;
    for (unsigned i=0;i<2048;++i) {
      state^=state<<13;state^=state>>17;state^=state<<5;
      const float noise=(static_cast<int>(state>>16)-32768)/32768.f;
      const float envelope=i>=256 ? .15f*std::exp(-static_cast<float>(i-256)/50.f) : 0.f;
      raw[i]=noise*(.0003f+envelope);
    }
  }
 private:
  void fft() {
    for (unsigned i=1,j=0;i<256;++i) {
      unsigned bit=128;
      for (;j&bit;bit>>=1) j^=bit;
      j^=bit;
      if (i<j) {std::swap(real_[i],real_[j]);std::swap(imag_[i],imag_[j]);}
    }
    for (unsigned length=2;length<=256;length<<=1) {
      for (unsigned base=0;base<256;base+=length) for (unsigned j=0;j<length/2;++j) {
        const unsigned a=base+j,b=a+length/2,w=j*256/length;
        const int32_t r=static_cast<int32_t>((static_cast<int64_t>(real_[b])*cnn_tables::twiddleRealQ30[w]
            -static_cast<int64_t>(imag_[b])*cnn_tables::twiddleImagQ30[w]+(1ll<<29))>>30);
        const int32_t im=static_cast<int32_t>((static_cast<int64_t>(real_[b])*cnn_tables::twiddleImagQ30[w]
            +static_cast<int64_t>(imag_[b])*cnn_tables::twiddleRealQ30[w]+(1ll<<29))>>30);
        real_[b]=real_[a]-r;imag_[b]=imag_[a]-im;real_[a]+=r;imag_[a]+=im;
      }
    }
  }
  // RMS-normalized windows have L2 norm <=sqrt(2048)*0.1. Every 256-point
  // FFT partial sum is bounded by 72.41, so Q24 fits signed int32 without
  // per-stage scaling. Q30 twiddles and int64 products retain input parity.
  int32_t normalized_[2048]{},real_[256]{},imag_[256]{};
  float power_[129]{};
};

class CnnStream {
 public:
  struct Candidate {uint64_t onset=0; double strength=0;};
  void reset() {
    inputCount_=produced_=0;firCursor_=head_=size_=0;frameSquare_=strengthSquare_=0;
    noise_=previous_=1e-4;last_=0;hasLast_=fault_=false;
    gate_={.6764115352973714,-1.3528230705947428,.6764115352973714,-1.2452189123904238,.4604272287990623};
    strength_={.5690355937288492,-1.1380711874576983,.5690355937288492,-.9428090415820631,.33333333333333326};
    std::memset(fir_,0,sizeof(fir_));
  }
  bool push(uint16_t adc) {
    if (fault_) return false;
    fir_[firCursor_]=static_cast<int16_t>(static_cast<int>(adc)-2048);
    const unsigned newest=firCursor_;
    if (++firCursor_==41) firCursor_=0;
    const uint64_t now=inputCount_++;
    if (now<20 || (now-20)%2) return false;
    int64_t filtered=0;
    for (unsigned tap=0;tap<sizeof(cnn_tables::firOffsets);++tap) {
      int index=static_cast<int>(newest)-cnn_tables::firOffsets[tap];
      if(index<0)index+=41;
      filtered+=static_cast<int64_t>(fir_[index])*cnn_tables::firQ30[tap];
    }
    const float value=static_cast<float>(filtered)*(1.f/2199023255552.f); // Q30 / ADC 2048
    ring_[produced_%ringCapacity]=value;++produced_;
    const double high=gate_.process(value),attack=strength_.process(value);
    frameSquare_+=high*high;strengthSquare_+=attack*attack;
    if (produced_%16) return false;
    const uint64_t start=produced_-16;
    const double rms=std::sqrt(frameSquare_/16),strengthRms=std::sqrt(strengthSquare_/16);
    frameSquare_=strengthSquare_=0;
    for (unsigned i=0;i<size_;++i) {
      auto &c=queue_[(head_+i)%16];
      if (start>=c.onset && start<c.onset+256) c.strength=std::max(c.strength,strengthRms);
    }
    bool proposed=false;
    if (start>=16000 && rms>=std::max(.002,2*noise_) && rms>=1.3*std::max(previous_,noise_)
        && (!hasLast_ || start-last_>=256)) {
      if (size_==16) {fault_=true;++drops_;return false;}
      queue_[(head_+size_)%16]={start,strengthRms};++size_;++proposals_;
      peakQueue_=std::max(peakQueue_,size_);last_=start;hasLast_=true;proposed=true;
    }
    if (start<16000) noise_+=.05*(rms-noise_);
    else noise_+=(rms>noise_?.01:.04)*(std::min(rms,noise_*3)-noise_);
    noise_=std::max(noise_,1e-5);previous_=rms;
    return proposed;
  }
  bool pop(float *raw,Candidate &candidate) {
    if (!size_ || produced_<queue_[head_].onset+1792 || fault_) return false;
    candidate=queue_[head_];
    const uint64_t start=candidate.onset-256;
    if (produced_>start+ringCapacity) {fault_=true;++drops_;return false;}
    for (unsigned i=0;i<2048;++i) raw[i]=ring_[(start+i)%ringCapacity];
    head_=(head_+1)%16;--size_;return true;
  }
  uint64_t watermark() const {
    uint64_t value=produced_>=1792?produced_-1792:0;
    if (size_) value=std::min(value,queue_[head_].onset-1);
    return value;
  }
  bool fault() const {return fault_;}
  unsigned queued() const {return size_;}
  unsigned peakQueue() const {return peakQueue_;}
  uint32_t drops() const {return drops_;}
  uint32_t proposals() const {return proposals_;}
  uint64_t samples() const {return produced_;}
 private:
  struct Biquad {
    double b0=0,b1=0,b2=0,a1=0,a2=0,z1=0,z2=0;
    double process(double x) {const double y=b0*x+z1;z1=b1*x-a1*y+z2;z2=b2*x-a2*y;return y;}
  } gate_,strength_;
  static constexpr unsigned ringCapacity=8192; // 1.024s, including pending post-trigger context
  int16_t fir_[41]{};
  float ring_[ringCapacity]{};
  Candidate queue_[16]{};
  uint64_t inputCount_=0,produced_=0,last_=0;
  unsigned firCursor_=0,head_=0,size_=0,peakQueue_=0;
  uint32_t proposals_=0,drops_=0;
  double noise_=1e-4,previous_=1e-4,frameSquare_=0,strengthSquare_=0;
  bool hasLast_=false,fault_=false;
};
