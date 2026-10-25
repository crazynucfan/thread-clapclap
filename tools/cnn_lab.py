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
GATE_SPEC = {'highpass_hz': 700, 'filter_order': 2, 'frame_samples': 16,
             'calibration_ms': 2000, 'minimum_rms': .002, 'noise_multiplier': 2.,
             'attack_ratio': 1.3, 'cooldown_ms': 32, 'cluster_ms': 120,
             'timestamp_highpass_hz': 1000, 'timestamp_lookahead_ms': 32}
MINING_SPEC = dict(GATE_SPEC, highpass_hz=1000, minimum_rms=.003,
                   noise_multiplier=2.5, attack_ratio=1.5)
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


def transient_candidates(samples, spec=None):
    """Sensitive event proposals plus attack strengths for timestamp association.

    A rejected sound must not blank the next clap for the pair's 160ms minimum
    gap. Use a 32ms proposal cooldown and associate accepted windows separately.
    Both filters run continuously, with zero initial state, after resampling.
    """
    import numpy as np
    from scipy.signal import butter, sosfilt
    spec = GATE_SPEC if spec is None else spec
    def band_rms(cutoff):
        signal = sosfilt(butter(2, cutoff, fs=RATE, btype='highpass', output='sos'), samples)
        return np.sqrt(np.mean(signal[:len(signal)//16*16].reshape(-1, 16)**2, axis=1))
    rms = band_rms(spec['highpass_hz'])
    strength = band_rms(spec['timestamp_highpass_hz'])
    noise, previous, last = 1e-4, 1e-4, -10000
    events = []
    for index, value in enumerate(rms):
        now, start = index * 2, index * 16
        if (now >= 2000 and value >= max(spec['minimum_rms'], spec['noise_multiplier'] * noise)
                and value >= spec['attack_ratio'] * max(previous, noise)
                and now - last >= spec['cooldown_ms']):
            if start >= PRE and start - PRE + WINDOW <= len(samples):
                events.append((start, float(np.max(strength[index:index+16]))))
                last = now
        if now < 2000:
            noise += .05 * (value - noise)
        else:
            noise += (.01 if value > noise else .04) * (min(value, noise * 3) - noise)
        noise = max(noise, 1e-5)
        previous = value
    return events


def transients(samples):
    return [onset for onset, _ in transient_candidates(samples)]


def window(samples, onset):
    import numpy as np
    if onset < PRE or onset - PRE + WINDOW > len(samples):
        raise ValueError('Event window extends beyond recording')
    result = np.array(samples[onset-PRE:onset-PRE+WINDOW], dtype=np.float32)
    return result - np.mean(result)


def spectrogram(windows, tf, mode='rms'):
    # Keep preprocessing outside the CNN so all exported model ops have INT8
    # kernels. MCU log-mel parity remains a separate deployment requirement.
    import numpy as np
    windows = np.asarray(windows, dtype=np.float32)
    if mode == 'preemphasis':
        windows = np.concatenate([np.zeros_like(windows[:, :1]), windows[:, 1:] - .97 * windows[:, :-1]], axis=1)
    if mode in ('rms', 'preemphasis'):
        rms = np.sqrt(np.mean(windows**2, axis=1, keepdims=True))
        windows = windows * (.1 / np.maximum(rms, 1e-5))
    stft = tf.signal.stft(windows, frame_length=256, frame_step=128, fft_length=256,
                          window_fn=tf.signal.hann_window, pad_end=False)
    mel = tf.signal.linear_to_mel_weight_matrix(32, 129, RATE, 125, 3800)
    power = tf.math.square(tf.abs(stft))
    features = tf.math.log(tf.matmul(power, mel) + 1e-6)
    if mode == 'contrast':
        features -= features[:, :1, :]
        return ((tf.clip_by_value(features, -8., 8.) + 8.) / 16.)[..., tf.newaxis]
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


def experiment_manifest(directory):
    """Prefer the exact training manifest over a mutable sibling manifest."""
    snapshot = directory / 'manifest.json'
    return snapshot if snapshot.exists() else directory.parent / 'manifest.json'


def save_manifest_snapshot(manifest_path, manifest, destination):
    snapshot = dict(manifest)
    snapshot['recordings'] = [dict(entry, annotations=os.path.relpath(
        (manifest_path.parent / entry['annotations']).resolve(), destination.parent.resolve()))
        for entry in manifest['recordings']]
    write_json(destination, snapshot)


def dataset(manifest_path, manifest=None):
    manifest = json.loads(manifest_path.read_text()) if manifest is None else manifest
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


def training_windows(records, seed):
    """Augment training recordings only; keep reviewed evaluation windows intact.

    Never auto-label quiet stretches of a positive recording. Hard negatives
    and background mixes come exclusively from confirmed no-clap train files.
    """
    import numpy as np
    from scipy.signal import butter, sosfilt
    rng = np.random.default_rng(seed)
    raw, labels = [], []
    negatives = [r for r in records if r['split'] == 'train' and r['negative_only']]
    if not negatives:
        raise ValueError('Training augmentation needs a verified no-clap training recording')
    def append(value, label):
        raw.append(np.asarray(value, dtype=np.float32)); labels.append(label)
    for record in records:
        if record['split'] != 'train':
            continue
        samples = record['samples']
        for onset, label in record['events']:
            append(window(samples, onset), label)
            for _ in range(8 if label == 0 else 2):
                jitter = int(rng.integers(-32, 13) if label == 0 else rng.integers(-16, 17)) * 16
                anchor = onset + jitter
                if anchor < PRE or anchor - PRE + WINDOW > len(samples):
                    anchor = onset
                value = window(samples, anchor) * float(np.exp(rng.uniform(np.log(.35 if label == 0 else .5), np.log(1.7))))
                if label == 0 and rng.random() < .65:
                    source = negatives[int(rng.integers(len(negatives)))]['samples']
                    noise = window(source, int(rng.integers(2 * RATE, len(source) - WINDOW)))
                    snr = float(rng.uniform(0, 30))
                    scale = float(np.sqrt(np.mean(value**2))) / (10**(snr/20) * max(float(np.sqrt(np.mean(noise**2))), 1e-5))
                    value = value + noise * scale
                append(np.clip(value, -1, 1), label)
        # Mine the stronger attack proposals rather than multiplying thousands
        # of near-identical weak-noise windows. Ordinary verified background is
        # already sampled separately; inference uses the more sensitive gate.
        proposals = [onset for onset, _ in transient_candidates(samples, MINING_SPEC)]
        if record['negative_only']:
            known = np.array([onset for onset, _ in record['events']])
            for onset in proposals:
                if len(known) and np.min(np.abs(known - onset)) < 64:
                    continue
                append(window(samples, onset), 1)
        else:
            for onset, label in record['events']:
                if label == 0:
                    nearby = [o for o in proposals if -64*8 <= o-onset <= 24*8]
                    for candidate in sorted(nearby, key=lambda o: abs(o-onset))[:2]:
                        if candidate != onset:
                            append(window(samples, candidate), 0)
    # Spectral balance, speed and a short delayed reflection model variation
    # between hands and room positions. Apply to both classes, never just claps.
    rng = np.random.default_rng(seed + 30)
    for record in records:
        if record['split'] != 'train':
            continue
        for onset, label in record['events']:
            for _ in range(12 if label == 0 else 2):
                value = window(record['samples'], onset)
                indices = PRE + (np.arange(WINDOW)-PRE) * float(rng.uniform(.8, 1.25))
                value = np.interp(indices, np.arange(WINDOW), value, left=0, right=0)
                value = sosfilt(butter(2, float(rng.uniform(650, 3900)), fs=RATE, output='sos'), value)
                if rng.random() < .5:
                    delay = int(rng.integers(32, 240))
                    value[delay:] += float(rng.uniform(.1, .5)) * value[:-delay]
                append(value, label)
    return np.stack(raw), np.array(labels)


ARCHITECTURES = ('flat', 'wide', 'ds-flat', 'ds-gap', 'ds-wide-flat', 'ds-wide-gap',
                 'ds3-bn-flat', 'ds3-bn-gap')


def build_model(tf, architecture='flat'):
    if architecture not in ARCHITECTURES:
        raise ValueError('Unknown CNN architecture: ' + architecture)
    if architecture.startswith(('ds-', 'ds3-')):
        batch_norm = architecture.startswith('ds3-bn-')
        channels = ((12, 24, 32, 32) if batch_norm else
                    (12, 24, 32) if architecture.startswith('ds-wide-') else (8, 16, 24))
        layers = [tf.keras.layers.Input(shape=(15, 32, 1))]
        def convolution(layer):
            layers.append(layer)
            if batch_norm:
                layers.extend([tf.keras.layers.BatchNormalization(), tf.keras.layers.ReLU()])
        convolution(tf.keras.layers.Conv2D(channels[0], 3, strides=2, padding='same',
            activation=None if batch_norm else 'relu', use_bias=not batch_norm))
        for index, channel in enumerate(channels[1:]):
            convolution(tf.keras.layers.DepthwiseConv2D(3, strides=2 if index<2 else 1, padding='same',
                activation=None if batch_norm else 'relu', use_bias=not batch_norm))
            convolution(tf.keras.layers.Conv2D(channel, 1,
                activation=None if batch_norm else 'relu', use_bias=not batch_norm))
        layers.append(tf.keras.layers.GlobalAveragePooling2D() if architecture.endswith('-gap')
                      else tf.keras.layers.Flatten())
        layers.append(tf.keras.layers.Dense(len(CLASSES), activation='softmax'))
        return tf.keras.Sequential(layers)
    channels = (12, 24, 32) if architecture == 'wide' else (8, 16, 24)
    strides = ((1, 2), (2, 2), (2, 2)) if architecture == 'wide' else ((2, 2),) * 3
    return tf.keras.Sequential([
        tf.keras.layers.Input(shape=(15, 32, 1)),
        *[tf.keras.layers.Conv2D(c, 3, strides=s, padding='same', activation='relu')
          for c, s in zip(channels, strides)],
        tf.keras.layers.Flatten(),
        tf.keras.layers.Dense(len(CLASSES), activation='softmax')])


def tensorflow():
    os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')
    import tensorflow as tf
    tf.config.threading.set_inter_op_parallelism_threads(1)
    tf.config.threading.set_intra_op_parallelism_threads(2)
    return tf


def convert(tf, model, representative):
    import numpy as np
    # A dynamic Flatten batch dimension introduces SHAPE/PACK/STRIDED_SLICE
    # operators. Inference processes one candidate at a time on the MCU.
    fixed_input = tf.keras.Input(batch_shape=(1, 15, 32, 1))
    fixed_model = tf.keras.Model(fixed_input, model(fixed_input, training=False))
    converter = tf.lite.TFLiteConverter.from_keras_model(fixed_model)
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
            or list(inputs['shape_signature']) != [1, 15, 32, 1]
            or list(outputs['shape']) != [1, len(CLASSES)]):
        raise ValueError('Expected INT8 [1,15,32,1] input and INT8 [1,2] output')
    if any(np.issubdtype(tensor['dtype'], np.floating) for tensor in runtime.get_tensor_details()):
        raise ValueError('Export still contains floating-point tensors')
    operators = {op['op_name'] for op in runtime._get_ops_details()} - {'DELEGATE'}
    if operators - {'CONV_2D', 'DEPTHWISE_CONV_2D', 'MEAN', 'RESHAPE', 'FULLY_CONNECTED', 'SOFTMAX'}:
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


PAIR_POLICIES = ('immediate', 'exact-two')


def pair_groups(onsets, policy='exact-two'):
    """160–599ms onset pairs; immediate emits once per burst, exact-two waits."""
    if policy not in PAIR_POLICIES:
        raise ValueError('Unknown pair policy: ' + str(policy))
    result, pending = [], []
    last = None
    for now in onsets:
        if last is not None and now - last >= 600:
            if policy == 'exact-two' and len(pending) == 2:
                result.append(pending)
            pending = []
        if last is None or now - last >= 160:
            if len(pending) < 3:
                pending.append(now)
                if policy == 'immediate' and len(pending) == 2:
                    result.append(pending.copy())
            last = now
    if policy == 'exact-two' and len(pending) == 2:
        result.append(pending)
    return result


def pairs(onsets, policy='exact-two'):
    return len(pair_groups(onsets, policy))


def associate(events, threshold, cluster_ms=120):
    """Merge overlapping accepted windows around one attack.

    Events are (onset_ms, score, clap_is_top_class, attack_strength). Anchor
    each cluster at its first candidate, rather than extending it indefinitely.
    The strongest next-32ms high-band attack supplies the timestamp; score
    breaks ties. An early window can see a future clap and score just as highly
    as the actual attack, so score alone is an unreliable timestamp selector.
    """
    selected, anchor, best = [], None, None
    for event in events:
        onset, score, top = event[:3]
        strength = event[3] if len(event) > 3 else score
        candidate = (onset, score, strength)
        if not top or score < threshold:
            continue
        if anchor is None or onset - anchor >= cluster_ms:
            if best is not None:
                selected.append(best[0])
            anchor, best = onset, candidate
        elif (strength, score) > (best[2], best[1]):
            best = candidate
    if best is not None:
        selected.append(best[0])
    return selected


def match_times(detected, expected, tolerance_ms=100):
    """One-to-one, order-preserving timestamp matching (single claps or pairs).

    Maximise matched events, then minimise timing error. A prediction cannot
    explain two annotated events. Pair matches must satisfy both timestamps.
    """
    def vector(item):
        return item if isinstance(item, (list, tuple)) else (item,)
    previous = [(0, 0., ()) for _ in range(len(expected) + 1)]
    for i, observed in enumerate(detected):
        current = [(0, 0., ())]
        for j, truth in enumerate(expected):
            choices = [previous[j+1], current[j]]
            a, b = vector(observed), vector(truth)
            errors = [abs(x-y) for x, y in zip(a, b)]
            if len(a) == len(b) and all(error <= tolerance_ms for error in errors):
                count, cost, matches = previous[j]
                choices.append((count+1, cost+sum(errors), matches+((i, j),)))
            current.append(max(choices, key=lambda value: (value[0], -value[1])))
        previous = current
    matches = previous[-1][2]
    used_d, used_e = {i for i, _ in matches}, {j for _, j in matches}
    return {'matches': [{'detected': detected[i], 'expected': expected[j]} for i, j in matches],
            'unmatched_detected': [item for i, item in enumerate(detected) if i not in used_d],
            'missed_expected': [item for j, item in enumerate(expected) if j not in used_e]}


def choose_threshold(positive_scores, negative_scores, negative_streams, positive_streams=(), *, pair_policy='exact-two'):
    """Select a validation operating point for the complete double-clap action.

    Window scores are (clap_score, top_class_is_clap). Each separate negative
    recording supplies candidate tuples consumed by associate(). Positive
    streams also supply reviewed clap times under 'truth'. Check their false
    actions too: an isolated-clap recording must not activate the switch.
    Never join streams across recordings.
    """
    if not positive_scores or not negative_scores or not negative_streams:
        raise ValueError('Threshold selection needs positive and negative validation windows '
                         'and a verified negative-only validation recording')
    choices = []
    for step in range(128, 257):
        threshold = step / 256
        accepted = [associate(stream, threshold) for stream in negative_streams]
        false_pairs = sum(pairs(onsets, pair_policy) for onsets in accepted)
        stream_matches, stream_total = 0, 0
        for stream in positive_streams:
            detected = associate(stream['scores'], threshold)
            truth = stream['truth']
            stream_matches += len(match_times(detected, truth)['matches'])
            stream_total += len(truth)
            false_pairs += len(match_times(pair_groups(detected, pair_policy), pair_groups(truth, pair_policy))['unmatched_detected'])
        if false_pairs:
            continue
        choices.append({
            'threshold': threshold,
            'clap_recall': sum(top and score >= threshold for score, top in positive_scores)
                / len(positive_scores),
            'stream_clap_recall': stream_matches / stream_total if stream_total else None,
            'false_clap_windows': sum(top and score >= threshold for score, top in negative_scores),
            'false_trigger_claps': sum(map(len, accepted)),
            'false_pairs': false_pairs,
        })
    if not choices:
        raise ValueError('No validation threshold rejected false double claps')
    # Maximise clap recall with zero observed validation false pairs. Among
    # equal-recall choices prefer fewer isolated false claps, then a higher
    # cutoff. Report those isolated errors: zero false pairs is not zero errors.
    return max(choices, key=lambda item: (item['stream_clap_recall']
                                        if item['stream_clap_recall'] is not None else item['clap_recall'],
                                        item['clap_recall'], -item['false_clap_windows'],
                                        -item['false_trigger_claps'], item['threshold']))


def score_stream(record, tf, runtime, inputs, outputs, frontend):
    import numpy as np
    proposals = transient_candidates(record['samples'])
    if not proposals:
        return []
    raw = np.stack([window(record['samples'], onset) for onset, _ in proposals])
    features = spectrogram(raw, tf, frontend).numpy()
    result = []
    for (onset, strength), feature in zip(proposals, features):
        scores = predict(runtime, inputs, outputs, feature)
        result.append((onset * 1000 / RATE, float(scores[0]), bool(np.argmax(scores) == 0), strength))
    return result


def replay(record, scores, threshold, pair_policy='exact-two'):
    accepted = associate(scores, threshold)
    detected = pair_groups(accepted, pair_policy)
    truth = [onset * 1000 / RATE for onset, label in sorted(record['events']) if label == 0]
    expected = pair_groups(truth, pair_policy)
    return {'recording': str(record['path']), 'split': record['split'],
            'negative_only': record['negative_only'], 'duration_s': record['duration_s'],
            'usable_negative_seconds': max(0, record['duration_s'] - 2 - (WINDOW-PRE)/RATE)
                if record['negative_only'] else 0,
            'candidate_count': len(scores), 'accepted_claps': accepted,
            'recognized_pairs': len(detected), 'expected_pairs_from_annotations': len(expected),
            'pair_matching': match_times(detected, expected),
            'clap_matching': match_times(accepted, truth),
            'annotation_review': record['annotation_review']}


def train(args):
    import numpy as np
    import scipy
    from scipy.signal import butter
    started = time.monotonic()
    trainer_sha256 = wave_lab.digest(Path(__file__))
    destination = wave_lab.private_path(args.output)
    if destination.exists():
        raise ValueError('Output directory already exists; choose a new experiment name')
    tf = tensorflow()
    tf.keras.utils.set_random_seed(args.seed)
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    records = dataset(args.manifest, manifest)
    frontend, architecture = getattr(args, 'frontend', 'rms'), getattr(args, 'architecture', 'flat')
    pair_policy = getattr(args, 'pair_policy', 'immediate')
    arrays, reviewed_counts = {}, {}
    for split in ('train', 'validation', 'test'):
        examples = [(record, onset, label) for record in records if record['split'] == split
                    for onset, label in record['events']]
        counts = Counter(label for _, _, label in examples)
        minimum = 20 if split == 'train' else 5
        if any(counts[label] < minimum for label in range(len(CLASSES))):
            raise ValueError(f'{split} needs at least {minimum} reviewed windows of each class; got {dict(counts)}')
        reviewed_counts[split] = {CLASSES[label]: counts[label] for label in range(len(CLASSES))}
        if split == 'train':
            raw, labels = training_windows(records, args.seed)
        else:
            raw = np.stack([window(record['samples'], onset) for record, onset, _ in examples])
            labels = np.array([label for _, _, label in examples])
        arrays[split] = (spectrogram(raw, tf, frontend).numpy(), labels)
    # Test recordings cannot select weights, early stopping or the operating cutoff.
    model = build_model(tf, architecture)
    model.compile(optimizer=tf.keras.optimizers.Adam(.001), loss='sparse_categorical_crossentropy', metrics=['accuracy'])
    options = tf.data.Options(); options.threading.private_threadpool_size = 1
    def batches(split, shuffle=False):
        features, labels = arrays[split]
        weights = {int(label): len(labels)/(len(CLASSES)*count) for label, count in Counter(labels).items()}
        data = tf.data.Dataset.from_tensor_slices((features, labels,
            np.array([weights[int(label)] for label in labels], dtype=np.float32)))
        if shuffle:
            data = data.shuffle(len(labels), seed=args.seed)
        return data.batch(32).with_options(options)
    history = model.fit(batches('train', True), validation_data=batches('validation'), epochs=args.epochs,
        callbacks=[tf.keras.callbacks.EarlyStopping(monitor='val_loss', patience=10, restore_best_weights=True)], verbose=2)
    x, y = arrays['train']
    representative = np.concatenate([x[np.flatnonzero(y == label)[
        np.linspace(0, np.sum(y == label)-1, min(100, np.sum(y == label))).astype(int)]]
        for label in range(len(CLASSES))])
    converted = convert(tf, model, representative)
    runtime, inputs, outputs, operators = interpreter(tf, converted)
    validation_scores = np.stack([predict(runtime, inputs, outputs, f) for f in arrays['validation'][0]])
    positive_scores, negative_scores, negative_streams, positive_streams = [], [], [], []
    streams = {}
    for scores, label in zip(validation_scores, arrays['validation'][1]):
        item = (float(scores[0]), bool(np.argmax(scores) == 0))
        (positive_scores if label == 0 else negative_scores).append(item)
    for record in records:
        if record['split'] != 'validation':
            continue
        stream = score_stream(record, tf, runtime, inputs, outputs, frontend)
        streams[str(record['path'])] = stream
        if record['negative_only']:
            negative_streams.append(stream)
        else:
            positive_streams.append({'scores': stream,
                'truth': [onset*1000/RATE for onset, label in sorted(record['events']) if label == 0]})
    operating_point = choose_threshold(positive_scores, negative_scores, negative_streams, positive_streams, pair_policy=pair_policy)
    threshold = operating_point['threshold']
    normalization = {
        'rms': 'window DC removal; scale waveform to RMS 0.1 with denominator floor 1e-5; log mel clipped [-16,4], scaled (log+16)/20',
        'preemphasis': 'window DC removal; first sample zero, x[n]-.97*x[n-1]; RMS 0.1 floor 1e-5; log mel [-16,4] scaled (log+16)/20',
        'contrast': 'window DC removal; log mel minus first log-mel frame per band; clip [-8,8], scale (contrast+8)/16',
    }
    spec = dict(SPEC, mode=frontend, normalisation=normalization[frontend])
    destination.mkdir(parents=True)
    save_manifest_snapshot(args.manifest, manifest, destination / 'manifest.json')
    protocol = {'schema': 2, 'seed': args.seed, 'test_role': getattr(args, 'test_role', 'held_out'), 'pair_policy': pair_policy,
                'frontend': spec, 'gate': GATE_SPEC, 'mining_gate': MINING_SPEC,
                'filter_sos_coefficients': {str(hz): butter(2, hz, fs=RATE, btype='highpass', output='sos').tolist()
                    for hz in (700, 1000)}, 'architecture': architecture,
                'parameters': model.count_params(), 'threshold': threshold,
                'validation_operating_point': operating_point, 'model_sha256': hashlib.sha256(converted).hexdigest(),
                'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                'manifest_snapshot_sha256': wave_lab.digest(destination / 'manifest.json'), 'trainer_sha256': trainer_sha256,
                'recordings': [{'sha256': r['sha256'], 'split': r['split'], 'group': r['group'],
                    'events_sha256': hashlib.sha256(json.dumps(sorted(r['events'])).encode()).hexdigest()}
                    for r in records]}
    # Persist the complete operating point before the first test prediction.
    write_json(destination / 'selection-frozen.json', protocol)
    (destination / 'clap.tflite').write_bytes(converted)
    model.save(destination / 'float.keras')
    write_json(destination / 'history.json', history.history)
    def recall(scores, labels):
        accepted = (np.argmax(scores, axis=1) == 0) & (scores[:, 0] >= threshold)
        return float(np.mean(accepted[labels == 0]))
    confusion = np.zeros((len(CLASSES), len(CLASSES)), dtype=int)
    test_scores = []
    for features, label in zip(*arrays['test']):
        scores = predict(runtime, inputs, outputs, features)
        test_scores.append(scores)
        confusion[int(label), int(np.argmax(scores))] += 1
    replay_rows = []
    for record in records:
        if record['split'] == 'train' and not record['negative_only']:
            continue
        key = str(record['path'])
        if key not in streams:
            streams[key] = score_stream(record, tf, runtime, inputs, outputs, frontend)
        row = replay(record, streams[key], threshold, pair_policy)
        replay_rows.append(row)
    write_json(destination / 'replay.json', {'protocol': protocol, 'records': replay_rows,
        'scores': streams, 'timing_tolerance_ms': 100,
        'training_replay_role': 'diagnostic only; seen by training, not independent evaluation'})
    test_rows = [r for r in replay_rows if r['split'] == 'test']
    false_pairs = sum(r['recognized_pairs'] for r in test_rows if r['negative_only'])
    negative_seconds = sum(r['usable_negative_seconds'] for r in test_rows)
    report = dict(protocol, classes=CLASSES,
        threshold_policy='maximise one-to-one full-stream validation clap recall, then annotated recall, with zero unmatched validation pair actions; break ties by isolated errors and higher cutoff',
        validation_negative_seconds=sum(r['usable_negative_seconds'] for r in replay_rows if r['split'] == 'validation'),
        validation_clap_recall_at_threshold=recall(validation_scores, arrays['validation'][1]),
        test_clap_recall_at_threshold=recall(np.stack(test_scores), arrays['test'][1]),
        reviewed_window_counts=reviewed_counts,
        augmented_training_counts={CLASSES[label]: int(np.sum(y == label)) for label in range(len(CLASSES))},
        augmentation={'positive_variants': 8, 'negative_variants': 2, 'noise_snr_db': [0,30],
            'noise_mix_probability': .65, 'positive_jitter_ms': [-64,24], 'negative_jitter_ms': [-32,32],
            'gain_positive': [.35,1.7], 'gain_negative': [.5,1.7],
            'spectral_variants_positive': 12, 'spectral_variants_negative': 2,
            'lowpass_hz': [650,3900], 'speed': [.8,1.25], 'reflection_delay_samples': [32,239],
            'sources': 'training only; background mixes and hard negatives from verified no-clap training files'},
        early_stopping='class-balanced validation loss, patience 10, restore best weights',
        epochs_run=len(history.history['loss']), model_bytes=len(converted), operators=operators,
        input_quantization=list(inputs['quantization']), output_quantization=list(outputs['quantization']),
        test_confusion_rows_actual=confusion.tolist(),
        test_positive_trials=[r for r in test_rows if not r['negative_only']],
        test_negative_seconds=negative_seconds, test_false_activations=false_pairs,
        test_false_activations_per_hour=false_pairs*3600/negative_seconds if negative_seconds else None,
        zero_event_95pct_upper_per_hour=-math.log(.05)*3600/negative_seconds if negative_seconds and not false_pairs else None,
        all_negative_replay_seconds=sum(r['usable_negative_seconds'] for r in replay_rows),
        all_negative_replay_false_actions=sum(r['recognized_pairs'] for r in replay_rows if r['negative_only']),
        tensor_arena_bytes=None, esp32c6_inference_us=None,
        runtime_status='Host reference only; MCU frontend, queueing, pair-rule parity and resource benchmarks remain required.',
        training_environment={'tensorflow': tf.__version__, 'numpy': np.__version__,
            'scipy': scipy.__version__, 'python': platform.python_version()},
        total_host_seconds=time.monotonic()-started)
    write_json(destination / 'report.json', report)
    rows = [', '.join(f'0x{value:02x}' for value in converted[i:i+16]) for i in range(0, len(converted), 16)]
    (destination / 'ClapModel.local.h').write_text('#pragma once\n#include <cstddef>\n'
        'alignas(16) inline constexpr unsigned char clapModel[] = {\n  ' + ',\n  '.join(rows)
        + '\n};\ninline constexpr size_t clapModelLength = sizeof(clapModel);\n')
    print(json.dumps(report, indent=2))


def evaluate(args):
    """Evaluate a frozen model/cutoff; never fit or select with these recordings."""
    destination = wave_lab.private_path(args.output)
    if destination.exists():
        raise ValueError('Evaluation output already exists; choose a new filename')
    source = json.loads((args.experiment / 'report.json').read_text())
    blob = (args.experiment / 'clap.tflite').read_bytes()
    if hashlib.sha256(blob).hexdigest() != source['model_sha256']:
        raise ValueError('Model does not match its report')
    if (source['classes'] != list(CLASSES) or source['gate'] != GATE_SPEC
            or source['frontend'].get('mode') not in ('rms','preemphasis','contrast')
            or any(source['frontend'].get(key) != value for key, value in SPEC.items() if key != 'normalisation')):
        raise ValueError('Evaluation requires the current binary frontend and gate')
    records = dataset(args.manifest)
    if any(record['split'] != 'test' for record in records):
        raise ValueError('Evaluation-only manifest must contain test recordings only')
    known = {record['sha256'] for record in source['recordings']}
    if args.test_role == 'held_out' and any(record['sha256'] in known for record in records):
        raise ValueError('These recordings already appear in the model experiment; evaluate them as diagnostic')
    policy = args.pair_policy or source.get('pair_policy', 'exact-two')
    tf = tensorflow()
    runtime, inputs, outputs, _ = interpreter(tf, blob)
    rows, streams = [], {}
    for record in records:
        scores = score_stream(record, tf, runtime, inputs, outputs, source['frontend']['mode'])
        streams[str(record['path'])] = scores
        rows.append(replay(record, scores, source['threshold'], policy))
    seconds = sum(row['usable_negative_seconds'] for row in rows)
    false_actions = sum(row['recognized_pairs'] for row in rows if row['negative_only'])
    report = {'schema': 1, 'evaluation_only': True, 'test_role': args.test_role,
        'model_sha256': source['model_sha256'], 'threshold': source['threshold'], 'pair_policy': policy,
        'selection_report_sha256': wave_lab.digest(args.experiment / 'report.json'),
        'manifest_sha256': wave_lab.digest(args.manifest), 'negative_seconds': seconds,
        'false_activations': false_actions, 'records': rows, 'scores': streams,
        'model_selection': 'Frozen weights and cutoff; these recordings do not select either.'}
    write_json(destination, report)
    print(json.dumps({key: value for key, value in report.items() if key not in ('records','scores')}, indent=2))


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
    training.add_argument('--frontend', choices=('rms', 'preemphasis', 'contrast'), default='rms')
    training.add_argument('--architecture', choices=ARCHITECTURES, default='flat')
    training.add_argument('--pair-policy', choices=PAIR_POLICIES, default='immediate')
    training.add_argument('--test-role', choices=('held_out', 'diagnostic'), default='held_out',
                          help='use diagnostic if these test recordings have already informed development')
    evaluation = commands.add_parser('evaluate', help='evaluate one frozen model/cutoff without training')
    evaluation.add_argument('experiment', type=Path)
    evaluation.add_argument('manifest', type=Path)
    evaluation.add_argument('--output', type=Path, required=True)
    evaluation.add_argument('--pair-policy', choices=PAIR_POLICIES)
    evaluation.add_argument('--test-role', choices=('held_out','diagnostic'), default='held_out')
    commands.add_parser('smoke', help='verify synthetic frontend/CNN/INT8 conversion; no saved model')
    args = parser.parse_args()
    if args.command == 'propose': propose(args.recording, args.output)
    elif args.command == 'train': train(args)
    elif args.command == 'evaluate': evaluate(args)
    else: smoke()


if __name__ == '__main__':
    try: main()
    except (ValueError, KeyError, TypeError, OSError, ImportError) as error:
        print(f'Error: {error}', file=sys.stderr); sys.exit(2)
    except KeyboardInterrupt: sys.exit(130)
