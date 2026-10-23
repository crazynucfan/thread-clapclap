#include <cstdlib>
#include <iostream>
#include "../firmware/ThreadClapClap/WaveCapture.h"
int main() {
  WaveCaptureFrame frame;
  frame.session = 42; frame.sequence = 2; frame.firstSample = 128;
  for (unsigned i = 0; i < 64; ++i) frame.samples[i] = i * 65;
  frame.checksum = captureCrc32(&frame, offsetof(WaveCaptureFrame, checksum));
  const auto *bytes = reinterpret_cast<const uint8_t *>(&frame);
  if (sizeof(frame) != 148 || bytes[0] != 'C' || bytes[3] != '1' || bytes[4] != 42
      || bytes[12] != 128 || frame.samples[63] != 4095) return EXIT_FAILURE;
  std::cout << "PASS waveform packet layout and 12-bit extremes\n";
  const uint32_t original = frame.checksum;
  frame.samples[4] ^= 1;
  if (captureCrc32(&frame, offsetof(WaveCaptureFrame, checksum)) == original) return EXIT_FAILURE;
  std::cout << "PASS waveform CRC detects changed audio samples\n";
}
