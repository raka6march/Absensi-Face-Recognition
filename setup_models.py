from pathlib import Path
from urllib.request import urlopen
ROOT=Path(__file__).parent/'models'
ROOT.mkdir(exist_ok=True)
BASE='https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/'
files={'yunet.onnx':('face_detection_yunet/face_detection_yunet_2023mar.onnx',200000),'sface.onnx':('face_recognition_sface/face_recognition_sface_2021dec.onnx',30000000)}
for name,(url,size) in files.items():
    target=ROOT/name
    if target.exists() and target.stat().st_size>=size: continue
    print('Mengunduh',name,flush=True)
    with urlopen(BASE+url,timeout=120) as response: data=response.read()
    if len(data)<size: raise RuntimeError('Unduhan model tidak lengkap: '+name)
    tmp=target.with_suffix('.tmp');tmp.write_bytes(data);tmp.replace(target)
for folder in ['face_detection_yunet','face_recognition_sface']:
    if (ROOT/(folder+'-LICENSE')).exists(): continue
    with urlopen('https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/'+folder+'/LICENSE',timeout=60) as r:
        (ROOT/(folder+'-LICENSE')).write_bytes(r.read())
print('Model siap. Setelah ini aplikasi dapat berjalan tanpa internet.')
