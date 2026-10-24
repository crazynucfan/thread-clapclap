#!/usr/bin/env python3
"""Generate private deployment assets from an explicitly selected CNN experiment."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import cnn_lab as cnn


def array(name, values, kind='float'):
    if kind == 'float':
        tokens = [f'{float(v):.9e}f' for v in values]
    else:
        tokens = [str(int(v)) for v in values]
    return f'inline constexpr {kind} {name}[] = {{\n  ' + ',\n  '.join(', '.join(tokens[i:i+8]) for i in range(0,len(tokens),8)) + '\n};\n'


def generate(experiment, destination):
    import numpy as np
    from scipy.signal import firwin
    report = json.loads((experiment / 'report.json').read_text())
    blob = (experiment / 'clap.tflite').read_bytes()
    if hashlib.sha256(blob).hexdigest() != report['model_sha256']:
        raise ValueError('Model does not match experiment report')
    if (report['frontend']['mode'] != 'rms' or report['architecture'] != 'flat'
            or report['classes'] != ['clap','non_clap'] or report['gate'] != cnn.GATE_SPEC):
        raise ValueError('Embedded frontend supports the RMS/flat binary model and current gate only')
    if report['frontend']['resampler'] != cnn.SPEC['resampler']:
        raise ValueError('Resampler mismatch')
    destination.mkdir(parents=True,exist_ok=True)
    tf = cnn.tensorflow()
    rt, inp, out, _ = cnn.interpreter(tf,blob)
    mel = tf.signal.linear_to_mel_weight_matrix(32,129,8000,125,3800).numpy()
    offsets, bins, weights = [0], [], []
    for band in range(32):
        indices = np.flatnonzero(mel[:,band]); bins.extend(indices); weights.extend(mel[indices,band]); offsets.append(len(bins))
    # scipy resample_poly(up=1,down=2): 41-tap Kaiser-5 FIR, centered at input 2*k.
    fir = firwin(41,.5,window=('kaiser',5.)).astype(np.float32)
    tables = '#pragma once\n#include <cstdint>\nnamespace cnn_tables {\n'
    tables += array('fir',fir)
    qfir=np.floor(fir.astype(np.float64)*2**30+.5).astype(np.int64)
    nonzero=np.flatnonzero(qfir)
    tables += array('firOffsets',nonzero,'uint8_t') + array('firQ30',qfir[nonzero],'int32_t')
    tables += array('hann',tf.signal.hann_window(256,periodic=True).numpy())
    tables += array('hannQ30',np.floor(tf.signal.hann_window(256,periodic=True).numpy().astype(np.float64)*2**30+.5),'int32_t')
    tables += array('twiddleReal',np.cos(2*np.pi*np.arange(128)/256))
    tables += array('twiddleImag',-np.sin(2*np.pi*np.arange(128)/256))
    tables += array('twiddleRealQ30',np.floor(np.cos(2*np.pi*np.arange(128)/256)*2**30+.5),'int32_t')
    tables += array('twiddleImagQ30',np.floor(-np.sin(2*np.pi*np.arange(128)/256)*2**30+.5),'int32_t')
    tables += array('melOffsets',offsets,'uint16_t') + array('melBins',bins,'uint8_t') + array('melWeights',weights)
    scale,zero=inp['quantization']
    # Quantized log-mel needs only the 255 boundaries between INT8 codes.
    # This removes 480 software logarithms per event on the FPU-less C6.
    boundaries=(np.arange(-127,128)-zero-.5)*scale*20-16
    tables += array('logQuantizationBoundaries',np.exp(boundaries)-1e-6)
    tables += f'inline constexpr float inputScale={scale:.9e}f;\ninline constexpr int inputZeroPoint={zero};\n'
    tables += '}\n'
    (destination/'CnnTables.local.h').write_text(tables)
    # A deterministic synthetic signal verifies target frontend/quantization.
    state=0x12345678; raw=[]
    for i in range(2048):
        state ^= (state << 13) & 0xffffffff;state ^= state >> 17;state ^= (state << 5) & 0xffffffff;state &= 0xffffffff
        noise=np.float32(((state>>16)-32768)/32768.)
        envelope=np.float32(.15*np.exp(-(i-256)/50.)) if i>=256 else np.float32(0)
        raw.append(np.float32(noise*np.float32(.0003+envelope)))
    feature=cnn.spectrogram(np.array(raw,np.float32)[None],tf).numpy()[0]
    scale,zero=inp['quantization'];quant=np.clip(np.floor(feature/scale+zero+.5),-128,127).astype(np.int8)
    score=cnn.predict(rt,inp,out,feature)
    # One reviewed positive feature checks model execution, without shipping raw audio.
    manifest=experiment.parent/'manifest.json'
    records=cnn.dataset(manifest)
    record=next(r for r in records if r['split']=='train' and not r['negative_only'])
    onset=next(o for o,l in record['events'] if l==0)
    positive=cnn.spectrogram(cnn.window(record['samples'],onset)[None],tf).numpy()[0]
    pq=np.clip(np.floor(positive/scale+zero+.5),-128,127).astype(np.int8)
    ps=cnn.predict(rt,inp,out,positive)
    header='#pragma once\n#include <cstddef>\n#include <cstdint>\n'
    header+='alignas(16) '+array('clapModel',blob,'unsigned char')
    header+='inline constexpr size_t clapModelLength=sizeof(clapModel);\n'
    header+=f'inline constexpr char clapModelSha256[]="{report["model_sha256"]}";\n'
    header+=f'inline constexpr float clapModelThreshold={report["threshold"]:.9e}f;\n'
    header+=array('cnnSyntheticFeatures',quant.flatten(),'int8_t')
    header+=array('cnnPositiveFeatures',pq.flatten(),'int8_t')
    header+=array('cnnExpectedScores',[score[0],score[1],ps[0],ps[1]])
    (destination/'ClapModel.local.h').write_text(header)
    (destination/'deployment.json').write_text(json.dumps({'schema':1,'experiment':str(experiment.resolve()),
        'model_sha256':report['model_sha256'],'threshold':report['threshold'],'test_role':report['test_role'],
        'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},indent=2)+'\n')
    print(f'Generated private model and frontend tables in {destination}')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('experiment',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    cnn.wave_lab.private_path(a.output)
    generate(a.experiment,a.output)

if __name__=='__main__':main()
