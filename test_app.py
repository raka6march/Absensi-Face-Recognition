import os, tempfile
os.environ['ABSENSI_DB']=tempfile.mktemp(suffix='.sqlite3')
from fastapi.testclient import TestClient
import app
client=TestClient(app.app)

def test_full_attendance_workflow():
    assert client.get('/').status_code==200
    assert client.post('/api/people',json={'name':'   '}).status_code==400
    pid=client.post('/api/people',json={'name':'Ahmad <Santri>','group_name':'Umum'}).json()['id']
    assert client.post('/api/attendance',json={'person_id':pid}).status_code==409
    with client.websocket_connect('/ws') as ws:
        sid=client.post('/api/sessions',json={'title':'Pengajian Selasa'}).json()['id']
        assert ws.receive_json()=={'type':'refresh'}
    assert client.post('/api/sessions',json={'title':'Duplikat'}).status_code==409
    assert client.post('/api/attendance',json={'person_id':999}).status_code==404
    assert client.post('/api/attendance',json={'person_id':pid}).json()['recorded']
    assert not client.post('/api/attendance',json={'person_id':pid}).json()['recorded']
    assert client.delete(f'/api/attendance/{sid}/{pid}').status_code==200
    assert client.get('/api/state').json()['rows'][0]['arrived_at'] is None
    assert client.post('/api/attendance',json={'person_id':pid}).json()['recorded']
    for i in range(100): client.post('/api/people',json={'name':f'Jamaah {i:03} dengan nama panjang','group_name':'Kelompok pengajian malam'})
    assert len(client.get('/api/state').json()['rows'])==101
    pdf=client.get(f'/api/reports/{sid}.pdf')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    open('/private/tmp/absensi-test-report.pdf','wb').write(pdf.content)
    assert client.get('/api/reports/999.pdf').status_code==404
    assert client.post(f'/api/sessions/{sid}/close').status_code==200
    assert client.post('/api/attendance',json={'person_id':pid}).status_code==409
    assert client.delete(f'/api/attendance/{sid}/{pid}').status_code==200
    client.post('/api/people',json={'name':'Jamaah baru'})
    assert len(client.get(f'/api/state?session_id={sid}').json()['rows'])==101
    sid2=client.post('/api/sessions',json={'title':'Pertemuan berikutnya'}).json()['id']
    assert len(client.get('/api/state').json()['rows'])==102
    assert not any(r['arrived_at'] for r in client.get('/api/state').json()['rows'])
    assert sid2!=sid
    assert client.post('/api/recognize',json={'image':'invalid'}).status_code in [400,503]

def test_models_load_and_unknown_face():
    if not app.snapshot()['model_ready']: return
    import numpy as np, cv2, base64
    image=np.zeros((480,640,3),dtype=np.uint8)
    _,encoded=cv2.imencode('.jpg',image)
    result=client.post('/api/recognize',json={'image':base64.b64encode(encoded).decode()})
    assert result.status_code==422
    assert app.detector is not None and app.recognizer is not None
