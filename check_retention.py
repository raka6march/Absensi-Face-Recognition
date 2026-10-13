import os,tempfile,sys
from datetime import datetime
from pathlib import Path
os.environ['ABSENSI_DB']=tempfile.mktemp(suffix='.sqlite3')
sys.path.insert(0,str(Path(__file__).parent))
import app
from fastapi.testclient import TestClient
c=TestClient(app.app)
pid=c.post('/api/people',json={'name':'Peserta retensi','phone':'081234567890'}).json()['id']
ids=[]
for title,start in [('sambung senin','2026-09-13T10:00:00+07:00'),('ngaji senin','2026-09-13T10:00:01+07:00'),('sambung selasa','2026-10-01T08:00:00+07:00')]:
 sid=c.post('/api/sessions',json={'title':title}).json()['id'];ids.append(sid)
 c.post('/api/attendance',json={'person_id':pid})
 c.post(f'/api/sessions/{sid}/close')
 with app.connect() as db: db.execute('UPDATE sessions SET started_at=? WHERE id=?',(start,sid))
active=c.post('/api/sessions',json={'title':'Sesi aktif lama'}).json()['id']
with app.connect() as db:
 db.execute('UPDATE sessions SET started_at=? WHERE id=?',('2026-08-01T00:00:00+07:00',active))
 db.execute('INSERT INTO attendance_status VALUES(?,?,?,?)',(ids[0],pid,'izin',app.now()))
reference=datetime.fromisoformat('2026-10-13T10:00:00+07:00')
assert app.cleanup_expired_sessions(reference)==1
assert {s['id'] for s in c.get('/api/state').json()['sessions']}=={ids[1],ids[2],active}
assert c.get(f'/api/state?session_id={ids[0]}').json()['selected'] is None
assert c.get(f'/api/reports/{ids[0]}.pdf').status_code==404
assert c.get('/api/state').json()['people'][0]['phone']=='081234567890'
with app.connect() as db:
 for table in ['attendance','attendance_status','roster']:
  assert db.execute(f'SELECT count(*) FROM {table} WHERE session_id=?',(ids[0],)).fetchone()[0]==0
 assert not db.execute('PRAGMA foreign_key_check').fetchall()
assert app.cleanup_expired_sessions(reference)==0
assert app.cleanup_expired_sessions(datetime.fromisoformat('2026-10-13T10:00:01+07:00'))==1
assert app.expires_at('2026-01-31T10:00:00+07:00').isoformat()=='2026-02-28T10:00:00+07:00'
assert app.expires_at('2028-01-31T10:00:00+07:00').isoformat()=='2028-02-29T10:00:00+07:00'
assert app.expires_at('2026-12-15T10:00:00+07:00').isoformat()=='2027-01-15T10:00:00+07:00'
print('Lulus: batas satu bulan kalender, tahun kabisat, akhir tahun, data relasi terhapus, sesi aktif dan peserta tetap tersimpan, pembersihan berulang aman.')
