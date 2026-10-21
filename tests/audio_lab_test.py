import importlib.util
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('audio_lab', ROOT/'tools/audio_lab.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)

def packet(sequence=0, sample_ms=1000):
    values = (b'CCF1', 42, sequence, sample_ms, 12345, 7., 21., .3, .2, .2, 0., .8, 26, 24, 0)
    raw = lab.PACKET.pack(*values)
    return raw[:-4] + struct.pack('<I', zlib.crc32(raw[:-4]))

def frame(sequence=0, sample_ms=1000):
    return next(x for x in lab.Decoder().feed(packet(sequence, sample_ms)) if x['type']=='frame')

class CaptureTests(unittest.TestCase):
    def test_fragmented_usb_with_logs(self):
        decoder = lab.Decoder()
        output=[]
        data=b'Audio status: okay\r\nCAPTURE_BEGIN 42 500 1000 9\n'+packet()+packet(1,1008)+b'CAPTURE_END 42 2 0 0\n'
        for byte in data:
            output.extend(decoder.feed(bytes([byte])))
        self.assertEqual([x['sequence'] for x in output if x['type']=='frame'],[0,1])
        self.assertEqual(len([x for x in output if x['type']=='control']),2)
        self.assertEqual(decoder.bad_crc,0)

    def test_corrupt_packet_resynchronises(self):
        corrupt=bytearray(packet());corrupt[25]^=0x80
        decoder=lab.Decoder()
        decoded=decoder.feed(corrupt+packet(1,1008))
        self.assertEqual([x['sequence'] for x in decoded if x['type']=='frame'],[1])
        self.assertEqual(decoder.bad_crc,1)
        with self.assertRaises(ValueError):lab.validate_frame(decoded[-1])

    def test_missing_frame_rejected(self):
        with self.assertRaises(ValueError):lab.validate_frame(frame(2,1016),frame())

    def test_clock_gap_rejected(self):
        with self.assertRaises(ValueError):lab.validate_frame(frame(1,1016),frame())

    def test_clock_wrap_supported(self):
        lab.validate_frame(frame(1,0),frame(0,0xfffffff8))

    def test_invalid_features_rejected(self):
        for key,value in [('rms',math.nan),('peak',math.inf),('flatness',1.2),('flux_bins',118)]:
            broken=frame();broken[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):lab.validate_frame(broken)

    def make_capture(self,path,positive=False,expected=None):
        metadata={'type':'session','schema':1,'session':42,'label':'synthetic',
                  'expected_clicks':expected,'duration_s':4,'detector_build_id':lab.detector_id()}
        items=[metadata]
        for seq in range(500):
            f=frame(seq,1000+seq*8)
            f.update(rms=7,peak=21,onset_flux=0,flux_bins=0)
            if positive:
                for onset in (2200,2504):
                    age=seq*8-onset
                    if 0<=age<40:
                        f.update(rms=[600,400,180,70,20][age//8],peak=[1800,1200,540,210,60][age//8],
                                 onset_flux=.9 if age==0 else .15,flux_bins=24 if age==0 else 0)
            items.append(f)
        items.append({'type':'complete','frames':500,'firmware_clicks':int(positive)})
        path.write_text(''.join(json.dumps(item)+'\n' for item in items))
        return items

    def test_incomplete_capture_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'take.jsonl';items=self.make_capture(path)
            path.write_text(''.join(json.dumps(x)+'\n' for x in items[:-1]))
            with self.assertRaises(ValueError):lab.load_capture(path)

    def test_wrong_duration_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'take.jsonl';items=self.make_capture(path)
            items[0]['duration_s']=5
            path.write_text(''.join(json.dumps(x)+'\n' for x in items))
            with self.assertRaises(ValueError):lab.load_capture(path)

    def test_native_replay_matches_firmware_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'take.jsonl';self.make_capture(path,positive=True,expected=1)
            run=subprocess.run([sys.executable,str(ROOT/'tools/audio_lab.py'),'replay',str(path)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr+run.stdout)
            result=json.loads(run.stdout)
            self.assertEqual(result['clicks'],1)
            self.assertTrue(result['firmware_consistent'])
            self.assertTrue(result['matched'])

    def test_native_replay_rejects_truncated_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'take.jsonl';self.make_capture(path)
            subprocess.run([sys.executable,str(ROOT/'tools/audio_lab.py'),'replay',str(path)],
                           capture_output=True,text=True,check=True)
            run=subprocess.run([str(ROOT/'.build/audio_replay'),'burst'],
                               input='1000 7 21 .3 .2 .2 0 26 .8 24\n1008 7',text=True,capture_output=True)
            self.assertEqual(run.returncode,2,run.stdout)

    def test_replay_reports_failed_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'take.jsonl';self.make_capture(path,expected=1)
            run=subprocess.run([sys.executable,str(ROOT/'tools/audio_lab.py'),'replay',str(path)],capture_output=True,text=True)
            self.assertEqual(run.returncode,1,run.stderr+run.stdout)
            self.assertFalse(json.loads(run.stdout)['matched'])

if __name__=='__main__':unittest.main()
