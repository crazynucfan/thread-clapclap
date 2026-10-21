#pragma once
#include <stddef.h>
#include <stdint.h>
#include "AudioFeatures.h"

// Little-endian v1 format. One 56-byte USB write per FFT hop; no waveform data.
struct AudioCaptureFrame {
  char magic[4] = {'C', 'C', 'F', '1'};
  uint32_t session = 0, sequence = 0, sampleMs = 0, uptimeMs = 0;
  float rms = 0, peak = 0, high = 0, flatness = 0, concentration = 0, clipping = 0, flux = 0;
  uint16_t occupiedBins = 0, fluxBins = 0;
  uint32_t checksum = 0;
};
static_assert(sizeof(AudioCaptureFrame) == 56 && sizeof(float) == 4, "Capture protocol layout changed");
static_assert(offsetof(AudioCaptureFrame, checksum) == 52, "Capture checksum offset changed");
inline uint32_t captureCrc32(const void *data, size_t size) {
  const auto *bytes = static_cast<const uint8_t *>(data);
  uint32_t crc = UINT32_MAX;
  for (size_t i = 0; i < size; ++i) {
    crc ^= bytes[i];
    for (unsigned bit = 0; bit < 8; ++bit) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}
inline AudioCaptureFrame captureFrame(const AudioFeatures &f, uint32_t session,
                                     uint32_t sequence, uint32_t sampleMs, uint32_t uptimeMs) {
  AudioCaptureFrame p;
  p.session = session; p.sequence = sequence; p.sampleMs = sampleMs; p.uptimeMs = uptimeMs;
  p.rms = f.rms; p.peak = f.peak; p.high = f.highRatio; p.flatness = f.flatness;
  p.concentration = f.concentration; p.clipping = f.clippedFraction; p.flux = f.onsetFlux;
  p.occupiedBins = f.occupiedBins; p.fluxBins = f.fluxBins;
  p.checksum = captureCrc32(&p, offsetof(AudioCaptureFrame, checksum));
  return p;
}
