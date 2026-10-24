"""Optional numeric frontend regressions; uses the isolated CNN environment."""
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import cnn_lab as cnn


def main():
    rng = np.random.default_rng(123)
    samples = rng.normal(0, 1e-5, 5 * cnn.RATE).astype(np.float32)
    for at in (3., 3.08, 3.24):
        start = int(at * cnn.RATE)
        age = np.arange(400)
        samples[start:start+len(age)] += (rng.normal(0, .2, len(age)) * np.exp(-age/45)).astype(np.float32)
    candidates = np.array(cnn.transients(samples)) / cnn.RATE
    for at in (3., 3.08, 3.24):
        assert np.min(np.abs(candidates-at)) <= .004, (at, candidates)
    hum = (.3*np.sin(2*np.pi*50*np.arange(5*cnn.RATE)/cnn.RATE)).astype(np.float32)
    assert not cnn.transients(hum), '50Hz hum generated transient proposals'

    tf = cnn.tensorflow()
    raw = np.stack([cnn.window(samples, 3*cnn.RATE), np.zeros(cnn.WINDOW, np.float32)])
    reference = cnn.spectrogram(raw, tf).numpy()
    scaled = cnn.spectrogram(raw * .02, tf).numpy()
    assert np.isfinite(reference).all()
    assert np.max(np.abs(reference-scaled)) < 1e-5, 'quiet signals hit a feature floor before normalization'

    # Evaluation recordings must not even supply waveform reads to augmentation.
    class EvaluationRecord(dict):
        def __getitem__(self, key):
            if key == 'samples':
                raise AssertionError('augmentation read evaluation audio')
            return super().__getitem__(key)
    positive = {'split': 'train', 'samples': samples, 'negative_only': False,
                'events': [(3*cnn.RATE, 0)]}
    negative_samples = rng.normal(0, .001, 5*cnn.RATE).astype(np.float32)
    negative = {'split': 'train', 'samples': negative_samples, 'negative_only': True,
                'events': [(3*cnn.RATE, 1)]}
    records = [positive, negative, EvaluationRecord(split='validation'), EvaluationRecord(split='test')]
    before = samples.copy()
    x, y = cnn.training_windows(records, 7)
    x2, y2 = cnn.training_windows(records, 7)
    assert np.array_equal(x, x2) and np.array_equal(y, y2), 'augmentation is not reproducible'
    assert np.array_equal(samples, before), 'augmentation modified source audio'
    assert np.isfinite(x).all() and set(y) == {0, 1}
    print('PASS 32ms proposal cooldown, paired attacks, hum rejection, quiet-feature normalization, training-only deterministic augmentation')


if __name__ == '__main__':
    main()
