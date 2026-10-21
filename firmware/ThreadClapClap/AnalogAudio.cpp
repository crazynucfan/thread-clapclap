#include <Arduino.h>
#include "AnalogAudio.h"
#include <soc/soc_caps.h>

esp_err_t AnalogAudio::begin(uint8_t pin, uint32_t sampleRate) {
  adc_unit_t unit;
  esp_err_t error = adc_continuous_io_to_channel(pin, &unit, &channel_);
  if (error != ESP_OK) return error;
  if (unit != ADC_UNIT_1) return ESP_ERR_INVALID_ARG;
  adc_continuous_handle_cfg_t handleConfig{};
  handleConfig.max_store_buf_size = 16384; // 256ms of raw DMA results at 16kHz
  handleConfig.conv_frame_size = samplesPerRead * SOC_ADC_DIGI_RESULT_BYTES;
  error = adc_continuous_new_handle(&handleConfig, &handle_);
  if (error != ESP_OK) return error;
  adc_digi_pattern_config_t pattern{};
  pattern.atten = ADC_ATTEN_DB_12;
  pattern.channel = channel_;
  pattern.unit = unit;
  pattern.bit_width = SOC_ADC_DIGI_MAX_BITWIDTH;
  adc_continuous_config_t config{};
  config.pattern_num = 1;
  config.adc_pattern = &pattern;
  config.sample_freq_hz = sampleRate;
  config.conv_mode = ADC_CONV_SINGLE_UNIT_1;
  config.format = ADC_DIGI_OUTPUT_FORMAT_TYPE2;
  error = adc_continuous_config(handle_, &config);
  if (error != ESP_OK) return error;
  adc_continuous_evt_cbs_t callbacks{};
  callbacks.on_pool_ovf = onOverflow;
  error = adc_continuous_register_event_callbacks(handle_, &callbacks, this);
  if (error != ESP_OK) return error;
  return adc_continuous_start(handle_);
}

esp_err_t AnalogAudio::read(uint16_t *samples, uint32_t &count) {
  alignas(4) uint8_t bytes[samplesPerRead * SOC_ADC_DIGI_RESULT_BYTES];
  uint32_t readBytes = 0;
  count = 0;
  const esp_err_t error = adc_continuous_read(handle_, bytes, sizeof(bytes), &readBytes, 0);
  if (error != ESP_OK) return error;
  for (uint32_t i = 0; i + SOC_ADC_DIGI_RESULT_BYTES <= readBytes; i += SOC_ADC_DIGI_RESULT_BYTES) {
    const auto *sample = reinterpret_cast<const adc_digi_output_data_t *>(bytes + i);
    // ESP32-C6 has one ADC unit; its TYPE2 result has no unit field.
    if (sample->type2.channel != channel_) return ESP_ERR_INVALID_RESPONSE;
    samples[count++] = sample->type2.data;
  }
  return ESP_OK;
}

esp_err_t AnalogAudio::recover() {
  esp_err_t error = adc_continuous_stop(handle_);
  if (error != ESP_OK) return error;
  error = adc_continuous_flush_pool(handle_);
  if (error != ESP_OK) return error;
  return adc_continuous_start(handle_);
}
