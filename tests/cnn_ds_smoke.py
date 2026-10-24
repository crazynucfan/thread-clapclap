"""Optional synthetic DS-CNN training/export regressions; no acoustic accuracy claim."""
from pathlib import Path
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import cnn_lab as cnn


def main():
    tf=cnn.tensorflow()
    raw=np.random.default_rng(4).normal(0,.03,(8,cnn.WINDOW)).astype(np.float32)
    features=cnn.spectrogram(raw,tf).numpy()
    expected={'flat':5114,'ds-flat':1258,'ds-gap':922,'ds-wide-flat':2106,'ds-wide-gap':1658,
              'ds3-bn-flat':3986,'ds3-bn-gap':3538}
    for architecture,count in expected.items():
        tf.keras.utils.set_random_seed(7)
        model=cnn.build_model(tf,architecture)
        assert model.count_params()==count,(architecture,model.count_params())
        model.compile(optimizer='adam',loss='sparse_categorical_crossentropy')
        model.train_on_batch(features,np.arange(8)%2)
        blob=cnn.convert(tf,model,features)
        rt,inp,out,ops=cnn.interpreter(tf,blob)
        values=cnn.predict(rt,inp,out,features[0])
        assert values.shape==(2,) and np.isfinite(values).all()
        assert ('DEPTHWISE_CONV_2D' in ops)==architecture.startswith('ds')
        assert ('MEAN' in ops)==architecture.endswith('gap')
        assert not set(ops)&{'MUL','ADD','SHAPE','PACK','STRIDED_SLICE'},ops
        print('PASS',architecture,'parameters',count,'bytes',len(blob),'ops',ops,flush=True)
    print('PASS DS-CNN synthetic training, batch-norm folding, static INT8 export and execution')

if __name__=='__main__':main()
