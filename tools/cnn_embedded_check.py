#!/usr/bin/env python3
"""Replay existing private PCM through the exact embedded C++ frontend."""
import argparse,json,struct,subprocess,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import cnn_lab as cnn
import wave_lab


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('experiment',type=Path);p.add_argument('--assets',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 wave_lab.private_path(a.output)
 binary=a.assets/'frontend-replay'
 subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pedantic','-I',str(a.assets),str(ROOT/'tools/cnn_frontend_replay.cpp'),'-o',str(binary)],check=True)
 gesture_binary=a.assets/'gesture-replay'
 subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pedantic',str(ROOT/'tools/cnn_gesture_replay.cpp'),'-o',str(gesture_binary)],check=True)
 tf=cnn.tensorflow();blob=(a.experiment/'clap.tflite').read_bytes();rt,inp,out,_=cnn.interpreter(tf,blob)
 report=json.loads((a.experiment/'report.json').read_text());threshold=report['threshold'];rows=[]
 reference_rows={r['recording']:r for r in json.loads((a.experiment/'replay.json').read_text())['records']}
 dtype=np.dtype([('onset','<u8'),('strength','<f8'),('features','i1',(480,))])
 for r in cnn.dataset(a.experiment.parent/'manifest.json'):
  if r['split']=='train' and not r['negative_only']:continue
  _,pcm=wave_lab.load_wave(r['path']);adc=(np.asarray(pcm,dtype=np.int32)//16+2048).astype('<u2')
  result=subprocess.run([str(binary)],input=adc.tobytes(),capture_output=True,check=True)
  events=np.frombuffer(result.stdout,dtype=dtype);expected=cnn.transient_candidates(r['samples']);expected=[e for e in expected if e[0]+1792<=len(r['samples'])-10]
  onsets=[int(e['onset']) for e in events]
  assert onsets==[o for o,s in expected],(r['path'].name,'proposal mismatch',len(onsets),len(expected))
  raw=np.stack([cnn.window(r['samples'],o) for o in onsets]);reference=cnn.spectrogram(raw,tf).numpy()
  scale,zero=inp['quantization'];quant=np.clip(np.floor(reference/scale+zero+.5),-128,127).astype(np.int8).reshape(-1,480)
  delta=np.abs(events['features'].astype(np.int16)-quant.astype(np.int16));max_delta=int(delta.max());changed=int(np.sum(delta!=0));assert max_delta<=1,(r['path'].name,max_delta)
  streams=[];max_score_delta=0;decisions_changed=0
  for event,feature in zip(events,reference):
   rt.set_tensor(inp['index'],event['features'].reshape(1,15,32,1));rt.invoke();os,oz=out['quantization'];score=(rt.get_tensor(out['index'])[0].astype(np.float32)-oz)*os
   original=cnn.predict(rt,inp,out,feature);max_score_delta=max(max_score_delta,float(np.max(np.abs(original-score))))
   decisions_changed+=bool((score[0]>=score[1] and score[0]>=threshold)!=(original[0]>=original[1] and original[0]>=threshold))
   streams.append((int(event['onset'])*1000/cnn.RATE,float(score[0]),bool(score[0]>=score[1]),float(event['strength'])))
  row=cnn.replay(r,streams,threshold);row.update(max_input_delta=max_delta,changed_input_values=changed,input_values=int(delta.size),max_score_delta=max_score_delta,changed_candidate_decisions=decisions_changed)
  packed=b''.join(struct.pack('<QfBd',int(onset*8),score,top,strength) for onset,score,top,strength in streams)
  actions=int(subprocess.run([str(gesture_binary),str(threshold)],input=packed,capture_output=True,check=True).stdout)
  assert actions==row['recognized_pairs'],(r['path'].name,'embedded gesture rule mismatch')
  row['embedded_gesture_actions']=actions
  rows.append(row);print('PARITY',r['path'].name,'candidates',len(onsets),'input_delta',max_delta,'changed',changed,'score_delta',max_score_delta,'decisions_changed',decisions_changed,'pairs',row['recognized_pairs'],flush=True)
  assert decisions_changed==0,(r['path'].name,'classifier decisions changed')
  if r['negative_only']:assert row['recognized_pairs']==0
  baseline=reference_rows[str(r['path'])]
  assert row['recognized_pairs']==baseline['recognized_pairs']
  assert len(row['pair_matching']['matches'])==len(baseline['pair_matching']['matches'])
  assert row['pair_matching']['missed_expected']==baseline['pair_matching']['missed_expected']
 a.output.write_text(json.dumps({'schema':1,'model_sha256':report['model_sha256'],'scope':'native C++ arithmetic and full-record replay; on-device runtime still requires its self-test and resource measurements','records':rows},indent=2)+'\n')
 print('PASS embedded C++ frontend parity and complete diagnostic replay')

if __name__=='__main__':main()
