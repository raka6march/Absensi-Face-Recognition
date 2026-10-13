import tempfile,os,sys
os.environ['ABSENSI_DB']=tempfile.mktemp(suffix='.sqlite3')
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import app
from fastapi.testclient import TestClient
c=TestClient(app.app)
p1=c.post('/api/people',json={'name':'Jamaah A','phone':'081234567890'}).json()['id']
p2=c.post('/api/people',json={'name':'Jamaah B'}).json()['id']
s1=c.post('/api/sessions',json={'title':'Sesi A'}).json()['id']
c.post('/api/attendance',json={'person_id':p1})
c.put(f'/api/attendance/{s1}/{p2}/status',json={'status':'izin'})
with c.websocket_connect('/ws') as ws:
    assert c.delete(f'/api/attendance/{s1}/{p1}').status_code==200
    assert ws.receive_json()=={'type':'refresh'}
rows=c.get('/api/state').json()['rows'];assert len(rows)==2
assert next(r for r in rows if r['id']==p1)['status']=='belum_hadir'
c.post('/api/attendance',json={'person_id':p1})
c.post(f'/api/sessions/{s1}/close')
assert c.delete(f'/api/attendance/{s1}/{p2}').status_code==200
assert next(r for r in c.get('/api/state').json()['rows'] if r['id']==p2)['status']=='tanpa_keterangan'
s2=c.post('/api/sessions',json={'title':'Sesi B'}).json()['id']
c.put(f'/api/attendance/{s2}/{p1}/status',json={'status':'izin'})
with c.websocket_connect('/ws') as ws:
    assert c.delete(f'/api/people/{p1}').status_code==200
    assert ws.receive_json()=={'type':'refresh'}
assert [p['id'] for p in c.get('/api/state').json()['people']]==[p2]
with app.connect() as db:
    for table in ['people','roster','attendance','attendance_status']:
        field='id' if table=='people' else 'person_id'
        assert db.execute(f'SELECT count(*) FROM {table} WHERE {field}=?',(p1,)).fetchone()[0]==0
    assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
assert c.delete(f'/api/people/{p1}').status_code==404
assert c.delete('/api/attendance/999/999').status_code==404
with c.websocket_connect('/ws') as ws:
    assert c.delete(f'/api/sessions/{s2}').status_code==200
    assert ws.receive_json()=={'type':'refresh'}
assert len(c.get('/api/state').json()['people'])==1
assert c.get('/api/state').json()['selected']['id']==s1
assert c.get(f'/api/state?session_id={s2}').json()['selected'] is None
with app.connect() as db:
    for table in ['roster','attendance','attendance_status']:
        assert db.execute(f'SELECT count(*) FROM {table} WHERE session_id=?',(s2,)).fetchone()[0]==0
    assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
assert c.delete(f'/api/sessions/{s2}').status_code==404
assert c.delete(f'/api/sessions/{s1}').status_code==200
assert c.get('/api/state').json()['sessions']==[]
assert c.get('/api/state').json()['selected'] is None
assert c.post('/api/sessions',json={'title':'Sesi baru'}).status_code==200
assert c.delete(f'/api/people/{p2}').status_code==200
assert c.get('/api/state').json()['rows']==[]
print('Lulus: hapus catatan sesi aktif/selesai, hapus jamaah lintas sesi, hapus pertemuan, integritas database, WebSocket, dan data tidak terkait tetap tersimpan.')
