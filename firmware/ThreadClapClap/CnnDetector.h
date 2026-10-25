#pragma once
#if defined(CLAP_USE_CNN) && CLAP_USE_CNN
#include <Arduino.h>
#include <esp_heap_caps.h>
#include <new>
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "ClapModel.local.h"
#include "CnnFrontend.h"
#include "CnnGesture.h"
#ifndef CLAP_MODEL_ARCHITECTURE
#define CLAP_MODEL_ARCHITECTURE "flat" // Compatibility with the original working export.
#endif
#ifndef CLAP_CNN_SHADOW
#define CLAP_CNN_SHADOW 0
#endif
#ifndef CLAP_CNN_IMMEDIATE_PAIR
#define CLAP_CNN_IMMEDIATE_PAIR 1
#endif
#if CLAP_CNN_SHADOW && __has_include("CnnBenchmark.local.h")
#include "CnnBenchmark.local.h"
#define CLAP_HAS_CNN_BENCHMARK_BANK 1
#endif

class CnnDetector {
 public:
  bool begin() {
    const auto *model=tflite::GetModel(clapModel);
    if (model->version()!=TFLITE_SCHEMA_VERSION) return failure("model schema");
    if (resolver_.AddConv2D()!=kTfLiteOk || resolver_.AddReshape()!=kTfLiteOk
        || resolver_.AddFullyConnected()!=kTfLiteOk || resolver_.AddSoftmax()!=kTfLiteOk
        || resolver_.AddDepthwiseConv2D()!=kTfLiteOk || resolver_.AddMean()!=kTfLiteOk) return failure("operator registration");
    arena_=static_cast<uint8_t *>(heap_caps_aligned_alloc(16,arenaBytes,MALLOC_CAP_8BIT|MALLOC_CAP_INTERNAL));
    if (!arena_) return failure("arena allocation");
    interpreter_=new(std::nothrow) tflite::MicroInterpreter(model,resolver_,arena_,arenaBytes);
    if (!interpreter_ || interpreter_->AllocateTensors()!=kTfLiteOk) return failure("tensor allocation");
    input_=interpreter_->input(0);output_=interpreter_->output(0);
    if (input_->type!=kTfLiteInt8 || input_->bytes!=480 || output_->type!=kTfLiteInt8 || output_->bytes!=2) return failure("input/output contract");
    if (input_->params.scale!=cnn_tables::inputScale || input_->params.zero_point!=cnn_tables::inputZeroPoint) return failure("quantization table contract");
    CnnFeatures::synthetic(raw_);features_.extract(raw_,input_->data.int8);
    int maxDelta=0,changed=0;
    for(unsigned i=0;i<480;++i) {
      const int delta=std::abs(static_cast<int>(input_->data.int8[i])-cnnSyntheticFeatures[i]);
      maxDelta=std::max(maxDelta,delta);changed+=delta!=0;
    }
    Serial.printf("CNN frontend self-test: maximum_input_delta=%d changed_values=%d/480\n",maxDelta,changed);
    if (maxDelta>1 || changed>24) return failure("frontend parity");
    if (!invoke() || !scoresMatch(0)) return failure("synthetic inference parity");
    std::memcpy(input_->data.int8,cnnPositiveFeatures,480);
    if (!invoke() || !scoresMatch(2)) return failure("positive inference parity");
#if CLAP_CNN_SHADOW
    if (!benchmark(CLAP_MODEL_ARCHITECTURE,clapModelSha256)) return failure("benchmark inference");
#if defined(CLAP_HAS_CNN_BENCHMARK_BANK)
    const uint32_t baseMin=benchmarkMinUs_,baseMedian=benchmarkMedianUs_,baseMax=benchmarkMaxUs_;
    unsigned benchmarkIndex=0;
    for (const auto &candidate:cnnBenchmarkModels) {
      if (!replaceModel(candidate.data) || !invoke()
          || std::abs(score(0)-candidate.clap)>2.f/256 || std::abs(score(1)-candidate.other)>2.f/256)
        return failure("benchmark model parity");
      if (!benchmark(candidate.architecture,candidate.sha256)) return failure("benchmark model inference");
      bankResults_[benchmarkIndex++]={benchmarkMinUs_,benchmarkMedianUs_,benchmarkMaxUs_,interpreter_->arena_used_bytes()};
    }
    if (!replaceModel(clapModel) || !invoke() || !scoresMatch(2)) return failure("benchmark control restore");
    benchmarkMinUs_=baseMin;benchmarkMedianUs_=baseMedian;benchmarkMaxUs_=baseMax;maxInferenceUs_=baseMax;
#endif
#endif
    Serial.printf("CNN ready: model=%s bytes=%u arena_used=%u arena_reserved=%u frontend_delta=%d changed=%d threshold=%.6f architecture=%s\n",
        clapModelSha256,static_cast<unsigned>(clapModelLength),static_cast<unsigned>(interpreter_->arena_used_bytes()),
        static_cast<unsigned>(arenaBytes),maxDelta,changed,clapModelThreshold,CLAP_MODEL_ARCHITECTURE);
    reset();return true;
  }
  void reset() {stream_.reset();gesture_.reset();}
  void push(uint16_t adc) {stream_.push(adc);}
  // Called only after ADC has been drained. Raw acquisition continues by DMA
  // during frontend/inference; never busy-wait for a future candidate window.
  bool service(bool diagnostics) {
    if (stream_.fault()) {
      reset();Serial.println("CNN queue overflow: gesture discarded; recalibrating.");return false;
    }
    if (failed_) return false;
    CnnStream::Candidate candidate;
    if (stream_.pop(raw_,candidate)) {
      const uint32_t started=micros();
      features_.extract(raw_,input_->data.int8);
      const uint32_t frontendUs=micros()-started;
      maxFrontendUs_=std::max(maxFrontendUs_,frontendUs);
      if (!invoke()) {failed_=true;gesture_.reset();Serial.println("CNN inference failed: actions disabled until reboot.");return false;}
      ++classified_;
      const float clap=score(0),other=score(1);
      const bool accepted=clap>=other && clap>=clapModelThreshold;
      accepted_+=accepted;
      gesture_.candidate(candidate.onset,clap,clap>=other,candidate.strength);
      maxServiceUs_=std::max(maxServiceUs_,static_cast<uint32_t>(micros()-started));
      if (diagnostics) Serial.printf("CNN %s: onset_ms=%llu score=%.6f queue=%u pending=%u frontend_us=%lu inference_us=%lu\n",
          accepted?"accepted":"rejected",static_cast<unsigned long long>(candidate.onset/8),clap,stream_.queued(),gesture_.pending(),
          static_cast<unsigned long>(frontendUs),static_cast<unsigned long>(lastInferenceUs_));
    }
    if (stream_.fault()) {reset();return false;}
    return gesture_.advance(stream_.watermark());
  }
  void status(uint32_t dmaOverflows,uint32_t acquisitionUs) const {
    Serial.printf("CNN status: classified=%lu accepted_candidates=%lu proposals=%lu queue=%u peak_queue=%u queue_drops=%lu dma_overflows=%lu max_frontend_us=%lu max_inference_us=%lu max_service_us=%lu max_acquisition_batch_us=%lu arena_used=%u free_8bit=%u largest_8bit=%u failed=%u model=%s architecture=%s shadow=%u benchmark_min_us=%lu benchmark_median_us=%lu benchmark_max_us=%lu pair_policy=%s min_gap_ms=160 max_gap_ms=599\n",
        static_cast<unsigned long>(classified_),static_cast<unsigned long>(accepted_),static_cast<unsigned long>(stream_.proposals()),
        stream_.queued(),stream_.peakQueue(),static_cast<unsigned long>(stream_.drops()),static_cast<unsigned long>(dmaOverflows),
        static_cast<unsigned long>(maxFrontendUs_),static_cast<unsigned long>(maxInferenceUs_),static_cast<unsigned long>(maxServiceUs_),static_cast<unsigned long>(acquisitionUs),
        static_cast<unsigned>(interpreter_->arena_used_bytes()),static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_8BIT)),
        static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_8BIT)),failed_,clapModelSha256,CLAP_MODEL_ARCHITECTURE,CLAP_CNN_SHADOW,
        static_cast<unsigned long>(benchmarkMinUs_),static_cast<unsigned long>(benchmarkMedianUs_),static_cast<unsigned long>(benchmarkMaxUs_),
        CLAP_CNN_IMMEDIATE_PAIR?"immediate":"exact-two");
#if defined(CLAP_HAS_CNN_BENCHMARK_BANK)
    Serial.printf("CNN BENCH: architecture=%s model=%s iterations=31 min_us=%lu median_us=%lu max_us=%lu arena_used=%u shadow=1\n",
        CLAP_MODEL_ARCHITECTURE,clapModelSha256,static_cast<unsigned long>(benchmarkMinUs_),static_cast<unsigned long>(benchmarkMedianUs_),
        static_cast<unsigned long>(benchmarkMaxUs_),static_cast<unsigned>(interpreter_->arena_used_bytes()));
    for (unsigned i=0;i<sizeof(cnnBenchmarkModels)/sizeof(cnnBenchmarkModels[0]);++i) {
      const auto &candidate=cnnBenchmarkModels[i];const auto &result=bankResults_[i];
      Serial.printf("CNN BENCH: architecture=%s model=%s iterations=31 min_us=%lu median_us=%lu max_us=%lu arena_used=%u shadow=1\n",
          candidate.architecture,candidate.sha256,static_cast<unsigned long>(result.minimum),static_cast<unsigned long>(result.median),
          static_cast<unsigned long>(result.maximum),static_cast<unsigned>(result.arena));
    }
#endif
  }
 private:
#if CLAP_CNN_SHADOW
  bool replaceModel(const unsigned char *bytes) {
    delete interpreter_;interpreter_=nullptr;input_=output_=nullptr;
    std::memset(arena_,0,arenaBytes);
    const auto *model=tflite::GetModel(bytes);
    if (model->version()!=TFLITE_SCHEMA_VERSION) return false;
    interpreter_=new(std::nothrow) tflite::MicroInterpreter(model,resolver_,arena_,arenaBytes);
    if (!interpreter_ || interpreter_->AllocateTensors()!=kTfLiteOk) return false;
    input_=interpreter_->input(0);output_=interpreter_->output(0);
    if (input_->type!=kTfLiteInt8 || input_->bytes!=480 || output_->type!=kTfLiteInt8 || output_->bytes!=2
        || input_->params.scale!=cnn_tables::inputScale || input_->params.zero_point!=cnn_tables::inputZeroPoint) return false;
    std::memcpy(input_->data.int8,cnnPositiveFeatures,480);
    return true;
  }
  bool benchmark(const char *architecture,const char *sha256) {
    // Before ADC starts, use the same fixed input and 31 repeats per model.
    // Thread/Matter tasks remain active and can preempt these measurements.
    uint32_t durations[31];
    for (auto &duration:durations) {
      if (!invoke()) return false;
      duration=lastInferenceUs_;
    }
    std::sort(durations,durations+31);
    benchmarkMinUs_=durations[0];benchmarkMedianUs_=durations[15];benchmarkMaxUs_=durations[30];
    Serial.printf("CNN BENCH: architecture=%s model=%s iterations=31 min_us=%lu median_us=%lu max_us=%lu arena_used=%u shadow=1\n",
        architecture,sha256,static_cast<unsigned long>(benchmarkMinUs_),static_cast<unsigned long>(benchmarkMedianUs_),
        static_cast<unsigned long>(benchmarkMaxUs_),static_cast<unsigned>(interpreter_->arena_used_bytes()));
    return true;
  }
#endif
  static bool failure(const char *stage) {Serial.printf("CNN self-test failed: %s\n",stage);return false;}
  float score(unsigned i) const {return (output_->data.int8[i]-output_->params.zero_point)*output_->params.scale;}
  bool scoresMatch(unsigned offset) const {
    return std::abs(score(0)-cnnExpectedScores[offset])<=2.f/256 && std::abs(score(1)-cnnExpectedScores[offset+1])<=2.f/256;
  }
  bool invoke() {
    const uint32_t started=micros();const auto status=interpreter_->Invoke();
    lastInferenceUs_=micros()-started;maxInferenceUs_=std::max(maxInferenceUs_,lastInferenceUs_);return status==kTfLiteOk;
  }
  // Each experimental architecture must pass allocation/self-test in this
  // bounded arena, with actual use reported before its live test.
  static constexpr size_t arenaBytes=8*1024;
  tflite::MicroMutableOpResolver<6> resolver_;
  tflite::MicroInterpreter *interpreter_=nullptr;
  TfLiteTensor *input_=nullptr,*output_=nullptr;
  uint8_t *arena_=nullptr;
  CnnStream stream_;CnnFeatures features_;
  CnnGesture gesture_{clapModelThreshold,CLAP_CNN_IMMEDIATE_PAIR?CnnGesture::Policy::Immediate:CnnGesture::Policy::ExactTwo};
  float raw_[2048]{};
  uint32_t classified_=0,accepted_=0,maxFrontendUs_=0,maxInferenceUs_=0,maxServiceUs_=0,lastInferenceUs_=0;
  uint32_t benchmarkMinUs_=0,benchmarkMedianUs_=0,benchmarkMaxUs_=0;
#if defined(CLAP_HAS_CNN_BENCHMARK_BANK)
  struct BenchmarkResult {uint32_t minimum=0,median=0,maximum=0;size_t arena=0;};
  BenchmarkResult bankResults_[sizeof(cnnBenchmarkModels)/sizeof(cnnBenchmarkModels[0])]{};
#endif
  bool failed_=false;
};
#endif
