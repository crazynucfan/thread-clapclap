#pragma once
#include "AudioCapture.h"

// Exact 12-bit ADC samples at 16kHz. Keep acquisition unchanged; the CNN
// frontend will low-pass and decimate to 8kHz. No lossy envelope conversion.
struct WaveCaptureFrame {
  char magic[4] = {'C', 'C', 'W', '1'};
  uint32_t session = 0, sequence = 0, firstSample = 0;
  uint16_t samples[64]{};
  uint32_t checksum = 0;
};
static_assert(sizeof(WaveCaptureFrame) == 148 && offsetof(WaveCaptureFrame, checksum) == 144,
              "Wave capture protocol layout changed");
