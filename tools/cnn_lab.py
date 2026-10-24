#!/usr/bin/env python3
"""Private event-triggered CNN experiment; training/export only, not deployed firmware.

Capture with wave_lab.py first. Training uses recording-group splits and a
fixed host reference frontend. No waveform, labels or model is published.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import time

import wave_lab

CLASSES = ('clap', 'non_clap')
LEGACY_NEGATIVES = frozenset(('knock', 'click', 'other'))
RATE = 8000
WINDOW = 2048
PRE = 256
SPEC = {'sample_rate': RATE, 'window_samples': WINDOW, 'pretrigger_samples': PRE,
        'frame_length': 256, 'frame_step': 128, 'fft_length': 256, 'mel_bands': 32,
        'mel_low_hz': 125, 'mel_high_hz': 3800, 'log_floor': 1e-6,
        'resampler': 'scipy-resample_poly-kaiser5-16k-to-8k',
        'normalisation': 'window DC removal; log(power-mel + 1e-6) clipped [-16, 4], scaled (log+16)/20'}


def class_index(label):
    """Keep reviewed older annotations usable with the binary model."""
    if label in LEGACY_NEGATIVES:
        return CLASSES.index('non_clap')
    if label not in CLASSES:
        raise ValueError('Invalid sound label: ' + str(label))
    return CLASSES.index(label)


def write_json(path, value):
    path = wave_lab.private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as output:
        json.dump(value, output, indent=2, allow_nan=False); output.write('\n')


def audio(path):
    import numpy as np
    from scipy.signal import resample_poly
    metadata, pcm = wave_lab.load_wave(path)
    return metadata, resample_poly(np.asarray(pcm, dtype=np.float32) / 32768, 1, 2).astype(np.float32)


def transients(samples):
    """Host reference gate; no continuous CNN inference. Two seconds to learn noise."""
    import numpy as np
    # Remove microphone DC before measuring 2ms RMS. The deployment frontend
    # must reproduce this filter and the gate before accuracy can be claimed.
    from scipy.signal import lfilter
    signal = lfilter([1, -1], [1, -.995], samples)
    noise, previous, last = 1e-4, 1e-4, -10 * RATE
    events = []
    for start in range(0, len(signal) - 15, 16):
        rms = float(np.sqrt(np.mean(signal[start:start+16] ** 2)))
        if start < 2 * RATE:
            noise += .05 * (rms - noise)
        elif (rms >= max(.0088, 4 * noise) and rms >= 2.2 * max(previous, noise)
              and start - last >= int(.160 * RATE)):
            if start >= PRE and start - PRE + WINDOW <= len(samples):
                events.append(start)
                last = start
        elif rms < max(.0088, 4 * noise):
            noise += (.01 if rms > noise else .05) * (rms - noise)
        noise = max(noise, 1e-5)
        previous = rms
    return events


def window(samples, onset):
    import numpy as np
    if onset < PRE or onset - PRE + WINDOW > len(samples):
        raise ValueError('Event window extends beyond recording')
    result = np.array(samples[onset-PRE:onset-PRE+WINDOW], dtype=np.float32)
    return result - np.mean(result)


def spectrogram(windows, tf):
    # Keep preprocessing outside the CNN so all exported model ops have INT8
    # kernels. MCU log-mel parity remains a separate deployment requirement.
    stft = tf.signal.stft(windows, frame_length=256, frame_step=128, fft_length=256,
                          window_fn=tf.signal.hann_window, pad_end=False)
    mel = tf.signal.linear_to_mel_weight_matrix(32, 129, RATE, 125, 3800)
    power = tf.math.square(tf.abs(stft))
    features = tf.math.log(tf.matmul(power, mel) + 1e-6)
    return ((tf.clip_by_value(features, -16., 4.) + 16.) / 20.)[..., tf.newaxis]


def propose(path, output):
    metadata, samples = audio(path)
    events = transients(samples)
    write_json(output, {'schema': 1, 'recording': os.path.relpath(path.resolve(), output.resolve().parent), 'wav_sha256': wave_lab.digest(path),
                        'group': metadata['group'], 'suggested_label': metadata['label'],
                        'negative_only': False,
                        'events': [{'onset_ms': start * 1000 / RATE, 'label': 'unlabelled'} for start in events]})
    print(f'{len(events)} proposed sounds. Edit each label to clap/non_clap; do not label from model predictions.')
    print('For a verified no-clap recording, set negative_only=true and label proposed sounds; this also supplies background windows.')


def dataset(manifest_path):
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema') != 1 or not manifest.get('recordings'):
        raise ValueError('Expected schema 1 manifest with recordings')
    groups, seen, recordings = {}, set(), []
    for entry in manifest['recordings']:
        path = (manifest_path.parent / entry['annotations']).resolve()
        annotations = json.loads(path.read_text())
        if annotations.get('schema') != 1 or type(annotations.get('negative_only', False)) is not bool:
            raise ValueError('Expected schema 1 annotations and a boolean negative_only field')
        recording = (path.parent / annotations['recording']).resolve()
        metadata, samples = audio(recording)
        group, split = metadata['group'], entry['split']
        if (not group or split not in ('train', 'validation', 'test')
                or group in groups and groups[group] != split):
            raise ValueError('Recording group must belong to exactly one train/validation/test split')
        groups[group] = split
        digest = wave_lab.digest(recording)
        if digest != annotations['wav_sha256'] or digest in seen:
            raise ValueError('Recording changed or duplicated (including copies with new filenames)')
        seen.add(digest)
        events = []
        last = -1
        for event in sorted(annotations['events'], key=lambda event: event['onset_ms']):
            if event['label'] == 'unlabelled':
                raise ValueError('Finish reviewing event labels before training: ' + path.name)
            label = class_index(event['label'])
            onset = event['onset_ms']
            if (isinstance(onset, bool) or not math.isfinite(onset)
                    or onset < 2000 or onset <= last):
                raise ValueError('Invalid class or event timestamp (first 2s are calibration)')
            sample = int(math.floor(onset * RATE / 1000 + .5))
            window(samples, sample)  # enforce complete context
            if annotations.get('negative_only') and label == CLASSES.index('clap'):
                raise ValueError('negative_only recordings cannot contain annotated claps')
            events.append((sample, label))
            last = onset
        # Only an explicitly confirmed negative may yield automatic background
        # windows; quiet sections of positive sessions are never auto-labelled.
        if annotations.get('negative_only') is True:
            for sample in range(2 * RATE, len(samples) - WINDOW, RATE):
                if all(abs(sample - onset) >= WINDOW for onset, _ in events):
                    events.append((sample, CLASSES.index('non_clap')))
        recordings.append({'path': recording, 'split': split, 'group': group, 'samples': samples,
                           'events': events, 'negative_only': annotations.get('negative_only') is True,
                           'duration_s': metadata['duration_s'], 'sha256': digest,
                           'annotation_review': annotations.get('review', {})})
    return recordings


def build_model(tf):
    return tf.keras.Sequential([
        tf.keras.layers.Input(shape=(15, 32, 1)),
        tf.keras.layers.Conv2D(8, 3, strides=2, padding='same', activation='relu'),
        tf.keras.layers.Conv2D(16, 3, strides=2, padding='same', activation='relu'),
        tf.keras.layers.Conv2D(24, 3, strides=2, padding='same', activation='relu'),
        tf.keras.layers.GlobalAveragePooling2D(),
        tf.keras.layers.Dense(len(CLASSES), activation='softmax')])


def tensorflow():
    os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')
    import tensorflow as tf
    tf.config.threading.set_inter_op_parallelism_threads(1)
    tf.config.threading.set_intra_op_parallelism_threads(2)
    return tf


def convert(tf, model, representative):
    import numpy as np
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = lambda: ([x[np.newaxis].astype(np.float32)] for x in representative)
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    result = converter.convert()
    if len(result) >= 80 * 1024:
        raise ValueError('Converted model exceeds the 80 KiB target')
    return result


def interpreter(tf, model):
    import numpy as np
    runtime = tf.lite.Interpreter(model_content=model, num_threads=1)
    runtime.allocate_tensors()
    inputs, outputs = runtime.get_input_details()[0], runtime.get_output_details()[0]
    if (inputs['dtype'] != np.int8 or outputs['dtype'] != np.int8
            or list(inputs['shape']) != [1, 15, 32, 1]
            or list(outputs['shape']) != [1, len(CLASSES)]):
        raise ValueError('Expected INT8 [1,15,32,1] input and INT8 [1,2] output')
    if any(np.issubdtype(tensor['dtype'], np.floating) for tensor in runtime.get_tensor_details()):
        raise ValueError('Export still contains floating-point tensors')
    operators = {op['op_name'] for op in runtime._get_ops_details()} - {'DELEGATE'}
    if operators - {'CONV_2D', 'MEAN', 'FULLY_CONNECTED', 'SOFTMAX'}:
        raise ValueError('Unexpected model operators: ' + str(operators))
    return runtime, inputs, outputs, sorted(operators)


def predict(runtime, inputs, outputs, features):
    import numpy as np
    scale, zero = inputs['quantization']
    # Explicit half-up rule, to reproduce in the future embedded frontend.
    quantized = np.clip(np.floor(features / scale + zero + .5), -128, 127).astype(np.int8)
    runtime.set_tensor(inputs['index'], quantized[np.newaxis])
    runtime.invoke()
    scale, zero = outputs['quantization']
    return (runtime.get_tensor(outputs['index'])[0].astype(np.float32) - zero) * scale


def pairs(onsets):
    """Proposed pair rule for host evaluation; timestamps are sound onsets."""
    total = count = 0
    last = None
    for now in onsets:
        if last is not None and now - last >= 600:
            total += count == 2
            count = 0
        if last is None or now - last >= 160:
            count = min(3, count + 1)
            last = now
    return total + (count == 2)


def choose_threshold(positive_scores, negative_scores, negative_streams):
    """Select a validation operating point for the complete double-clap action.

    Window scores are (clap_score, top_class_is_clap). Each separate negative
    recording supplies (onset_ms, clap_score, top_class_is_clap) events. Do not
    join streams: events in different recordings cannot form a pair.
    """
    if not positive_scores or not negative_scores or not negative_streams:
        raise ValueError('Threshold selection needs positive and negative validation windows '
                         'and a verified negative-only validation recording')
    choices = []
    for step in range(128, 257):
        threshold = step / 256
        accepted = [[onset for onset, score, top in stream if top and score >= threshold]
                    for stream in negative_streams]
        false_pairs = sum(pairs(onsets) for onsets in accepted)
        if false_pairs:
            continue
        choices.append({
            'threshold': threshold,
            'clap_recall': sum(top and score >= threshold for score, top in positive_scores)
                / len(positive_scores),
            'false_clap_windows': sum(top and score >= threshold for score, top in negative_scores),
            'false_trigger_claps': sum(map(len, accepted)),
            'false_pairs': false_pairs,
        })
    if not choices:
        raise ValueError('No validation threshold rejected false double claps')
    # Maximise clap recall with zero observed validation false pairs. Among
    # equal-recall choices prefer fewer isolated false claps, then a higher
    # cutoff. Report those isolated errors: zero false pairs is not zero errors.
    return max(choices, key=lambda item: (item['clap_recall'], -item['false_clap_windows'],
                                        -item['false_trigger_claps'], item['threshold']))


def train(args):
    import numpy as np
    destination = wave_lab.private_path(args.output)
    if destination.exists(): raise ValueError('Output directory already exists; choose a new experiment name')
    tf = tensorflow()
    tf.keras.utils.set_random_seed(args.seed)
    records = dataset(args.manifest)
    arrays = {}
    for split in ('train', 'validation', 'test'):
        examples = [(record, onset, label) for record in records if record['split'] == split
                    for onset, label in record['events']]
        counts = Counter(label for _, _, label in examples)
        minimum = 20 if split == 'train' else 5
        if any(counts[label] < minimum for label in range(len(CLASSES))):
            raise ValueError(f'{split} needs at least {minimum} reviewed windows of each class; got {dict(counts)}')
        raw = np.stack([window(record['samples'], onset) for record, onset, _ in examples])
        arrays[split] = (spectrogram(raw, tf).numpy(), np.array([label for _, _, label in examples]))
    # Test labels and features do not select weights, stopping point or threshold.
    model = build_model(tf)
    model.compile(optimizer=tf.keras.optimizers.Adam(.001), loss='sparse_categorical_crossentropy', metrics=['accuracy'])
    x, y = arrays['train']
    weights = {int(label): len(y) / (len(CLASSES) * count) for label, count in Counter(y).items()}
    # Use bounded tf.data threading on laptops and CI workers.
    options = tf.data.Options(); options.threading.private_threadpool_size = 1
    def batches(split, shuffle=False):
        features, labels = arrays[split]
        data = tf.data.Dataset.from_tensor_slices((features, labels))
        if shuffle: data = data.shuffle(len(labels), seed=args.seed)
        return data.batch(16).with_options(options)
    # Class weighting is applied via explicit training weights in the dataset.
    training = tf.data.Dataset.from_tensor_slices((x, y, np.array([weights[int(v)] for v in y], dtype=np.float32)))
    training = training.shuffle(len(y), seed=args.seed).batch(16).with_options(options)
    model.fit(training, validation_data=batches('validation'), epochs=args.epochs,
              callbacks=[tf.keras.callbacks.EarlyStopping(monitor='val_loss', patience=8, restore_best_weights=True)], verbose=2)
    representative = np.concatenate([x[y == label][:50] for label in range(len(CLASSES))])
    converted = convert(tf, model, representative)
    runtime, inputs, outputs, operators = interpreter(tf, converted)
    validation_scores = np.stack([predict(runtime, inputs, outputs, f) for f in arrays['validation'][0]])
    positive_scores, negative_scores, negative_streams = [], [], []
    for scores, label in zip(validation_scores, arrays['validation'][1]):
        item = (float(scores[0]), bool(np.argmax(scores) == 0))
        (positive_scores if label == 0 else negative_scores).append(item)
    validation_negative_seconds = 0
    for record in records:
        if record['split'] != 'validation' or not record['negative_only']:
            continue
        stream = []
        for onset in transients(record['samples']):
            features = spectrogram(window(record['samples'], onset)[np.newaxis], tf).numpy()[0]
            scores = predict(runtime, inputs, outputs, features)
            stream.append((onset * 1000 / RATE, float(scores[0]), bool(np.argmax(scores) == 0)))
        negative_streams.append(stream)
        validation_negative_seconds += max(0, record['duration_s'] - 2 - (WINDOW-PRE) / RATE)
    # All operating-point decisions use validation data and finish before any
    # test predictions. The full trigger/classifier/pair path determines false
    # actions; a single isolated false clap need not disable valid pairs.
    operating_point = choose_threshold(positive_scores, negative_scores, negative_streams)
    threshold = operating_point['threshold']
    def recall(scores, labels):
        positive = labels == 0
        accepted = (np.argmax(scores, axis=1) == 0) & (scores[:, 0] >= threshold)
        return float(np.mean(accepted[positive]))
    confusion = np.zeros((len(CLASSES), len(CLASSES)), dtype=int)
    test_scores = []
    for features, label in zip(*arrays['test']):
        scores = predict(runtime, inputs, outputs, features)
        test_scores.append(scores)
        confusion[int(label), int(np.argmax(scores))] += 1
    false_pairs = 0
    negative_seconds = 0
    positive_trials = []
    for record in records:
        if record['split'] != 'test': continue
        proposed = transients(record['samples'])
        recognized = []
        for onset in proposed:
            features = spectrogram(window(record['samples'], onset)[np.newaxis], tf).numpy()[0]
            score = predict(runtime, inputs, outputs, features)
            if int(np.argmax(score)) == 0 and score[0] >= threshold:
                recognized.append(onset * 1000 / RATE)
        count = pairs(recognized)
        if record['negative_only']:
            false_pairs += count
            negative_seconds += max(0, record['duration_s'] - 2 - (WINDOW-PRE) / RATE)
        else:
            expected = pairs([onset * 1000 / RATE for onset, label in sorted(record['events']) if label == 0])
            positive_trials.append({'recording': str(record['path']), 'expected_pairs_from_annotations': expected,
                                    'recognized_pairs': count,
                                    'annotation_review': record['annotation_review']})
    report = {'schema': 1, 'classes': CLASSES, 'frontend': SPEC, 'threshold': threshold,
              'test_role': getattr(args, 'test_role', 'held_out'),
              'threshold_policy': 'maximise validation clap recall with zero observed full-record '
                  'validation false pairs; break ties by isolated errors and higher cutoff',
              'validation_operating_point': operating_point,
              'validation_negative_seconds': validation_negative_seconds,
              'validation_clap_recall_at_threshold': recall(validation_scores, arrays['validation'][1]),
              'test_clap_recall_at_threshold': recall(np.stack(test_scores), arrays['test'][1]),
              'model_bytes': len(converted), 'operators': operators, 'test_confusion_rows_actual': confusion.tolist(),
              'test_positive_trials': positive_trials, 'test_negative_seconds': negative_seconds,
              'test_false_activations': false_pairs,
              'test_false_activations_per_hour': false_pairs * 3600 / negative_seconds if negative_seconds else None,
              'zero_event_95pct_upper_per_hour': -math.log(.05) * 3600 / negative_seconds
                  if negative_seconds and not false_pairs else None,
              'tensor_arena_bytes': None, 'esp32c6_inference_us': None,
              'runtime_status': 'Host reference only; MCU frontend, queueing, pair-rule parity and resource benchmarks remain required.',
              'model_sha256': hashlib.sha256(converted).hexdigest(),
              'training_environment': {'tensorflow': tf.__version__, 'python': platform.python_version()}}
    destination.mkdir(parents=True)
    (destination / 'clap.tflite').write_bytes(converted)
    write_json(destination / 'report.json', report)
    # C array is private and deliberately not linked into the current firmware.
    rows = [', '.join(f'0x{value:02x}' for value in converted[i:i+16]) for i in range(0, len(converted), 16)]
    (destination / 'ClapModel.local.h').write_text('#pragma once\n#include <cstddef>\n'
        'alignas(16) inline constexpr unsigned char clapModel[] = {\n  ' + ',\n  '.join(rows)
        + '\n};\ninline constexpr size_t clapModelLength = sizeof(clapModel);\n')
    print(json.dumps(report, indent=2))


def smoke():
    """Synthetic plumbing check, never an accuracy benchmark or shipped model."""
    import numpy as np
    tf = tensorflow()
    tf.keras.utils.set_random_seed(1)
    model = build_model(tf)
    raw = np.random.default_rng(1).normal(0, .03, (8, WINDOW)).astype(np.float32)
    features = spectrogram(raw, tf).numpy()
    model.compile(optimizer='adam', loss='sparse_categorical_crossentropy')
    model.train_on_batch(features, np.arange(8) % len(CLASSES))
    converted = convert(tf, model, features)
    runtime, inputs, outputs, operators = interpreter(tf, converted)
    before = time.perf_counter()
    values = predict(runtime, inputs, outputs, features[0])
    elapsed = time.perf_counter() - before
    assert features.shape == (8, 15, 32, 1) and values.shape == (len(CLASSES),)
    assert np.isfinite(values).all()
    print(json.dumps({'synthetic_plumbing_only': True, 'model_bytes': len(converted),
                      'operators': operators, 'host_first_invoke_us': int(elapsed * 1e6),
                      'esp32c6_inference_us': None, 'tensor_arena_bytes': None}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    propose_parser = commands.add_parser('propose', help='propose event windows for manual review')
    propose_parser.add_argument('recording', type=Path)
    propose_parser.add_argument('--output', type=Path, required=True)
    training = commands.add_parser('train', help='train, quantize and evaluate recording-group-held-out data')
    training.add_argument('manifest', type=Path)
    training.add_argument('--output', type=Path, required=True)
    training.add_argument('--epochs', type=int, choices=range(1, 501), default=80)
    training.add_argument('--seed', type=int, default=7)
    training.add_argument('--test-role', choices=('held_out', 'diagnostic'), default='held_out',
                          help='use diagnostic if these test recordings have already informed development')
    commands.add_parser('smoke', help='verify synthetic frontend/CNN/INT8 conversion; no saved model')
    args = parser.parse_args()
    if args.command == 'propose': propose(args.recording, args.output)
    elif args.command == 'train': train(args)
    else: smoke()


if __name__ == '__main__':
    try: main()
    except (ValueError, KeyError, TypeError, OSError, ImportError) as error:
        print(f'Error: {error}', file=sys.stderr); sys.exit(2)
    except KeyboardInterrupt: sys.exit(130)
