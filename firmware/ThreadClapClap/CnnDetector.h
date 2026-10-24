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

class CnnDetector {
 public:
  bool begin() {
    const auto *model=tflite::GetModel(clapModel);
    if (model->version()!=TFLITE_SCHEMA_VERSION) return failure("model schema");
    if (resolver_.AddConv2D()!=kTfLiteOk || resolver_.AddReshape()!=kTfLiteOk
        || resolver_.AddFullyConnected()!=kTfLiteOk || resolver_.AddSoftmax()!=kTfLiteOk) return failure("operator registration");
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
    Serial.printf("CNN ready: model=%s bytes=%u arena_used=%u arena_reserved=%u frontend_delta=%d changed=%d threshold=%.6f\n",
        clapModelSha256,static_cast<unsigned>(clapModelLength),static_cast<unsigned>(interpreter_->arena_used_bytes()),
        static_cast<unsigned>(arenaBytes),maxDelta,changed,clapModelThreshold);
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
    Serial.printf("CNN status: classified=%lu accepted_candidates=%lu proposals=%lu queue=%u peak_queue=%u queue_drops=%lu dma_overflows=%lu max_frontend_us=%lu max_inference_us=%lu max_service_us=%lu max_acquisition_batch_us=%lu arena_used=%u free_8bit=%u largest_8bit=%u failed=%u model=%s\n",
        static_cast<unsigned long>(classified_),static_cast<unsigned long>(accepted_),static_cast<unsigned long>(stream_.proposals()),
        stream_.queued(),stream_.peakQueue(),static_cast<unsigned long>(stream_.drops()),static_cast<unsigned long>(dmaOverflows),
        static_cast<unsigned long>(maxFrontendUs_),static_cast<unsigned long>(maxInferenceUs_),static_cast<unsigned long>(maxServiceUs_),static_cast<unsigned long>(acquisitionUs),
        static_cast<unsigned>(interpreter_->arena_used_bytes()),static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_8BIT)),
        static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_8BIT)),failed_,clapModelSha256);
  }
 private:
  static bool failure(const char *stage) {Serial.printf("CNN self-test failed: %s\n",stage);return false;}
  float score(unsigned i) const {return (output_->data.int8[i]-output_->params.zero_point)*output_->params.scale;}
  bool scoresMatch(unsigned offset) const {
    return std::abs(score(0)-cnnExpectedScores[offset])<=2.f/256 && std::abs(score(1)-cnnExpectedScores[offset+1])<=2.f/256;
  }
  bool invoke() {
    const uint32_t started=micros();const auto status=interpreter_->Invoke();
    lastInferenceUs_=micros()-started;maxInferenceUs_=std::max(maxInferenceUs_,lastInferenceUs_);return status==kTfLiteOk;
  }
  // Measured 3,052 bytes for this fixed INT8 architecture; leave >4KiB margin.
  static constexpr size_t arenaBytes=8*1024;
  tflite::MicroMutableOpResolver<4> resolver_;
  tflite::MicroInterpreter *interpreter_=nullptr;
  TfLiteTensor *input_=nullptr,*output_=nullptr;
  uint8_t *arena_=nullptr;
  CnnStream stream_;CnnFeatures features_;CnnGesture gesture_{clapModelThreshold};
  float raw_[2048]{};
  uint32_t classified_=0,accepted_=0,maxFrontendUs_=0,maxInferenceUs_=0,maxServiceUs_=0,lastInferenceUs_=0;
  bool failed_=false;
};
#endif
