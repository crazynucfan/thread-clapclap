#pragma once

#include <esp_adc/adc_continuous.h>
#include <esp_attr.h>
#include <stdint.h>

class AnalogAudio {
 public:
  static constexpr uint32_t samplesPerRead = 128;
  esp_err_t begin(uint8_t pin, uint32_t sampleRate);
  esp_err_t read(uint16_t *samples, uint32_t &count);
  esp_err_t recover();
  uint32_t overflows() const { return __atomic_load_n(&overflows_, __ATOMIC_RELAXED); }

 private:
  static bool ARDUINO_ISR_ATTR onOverflow(adc_continuous_handle_t,
                                        const adc_continuous_evt_data_t *, void *context) {
    auto *audio = static_cast<AnalogAudio *>(context);
    __atomic_fetch_add(&audio->overflows_, 1u, __ATOMIC_RELAXED);
    return false;
  }
  adc_continuous_handle_t handle_ = nullptr;
  adc_channel_t channel_ = ADC_CHANNEL_0;
  uint32_t overflows_ = 0;
};
