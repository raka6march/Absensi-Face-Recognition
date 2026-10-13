import asyncio, base64, calendar, io, json, logging, os, re, sqlite3, threading, time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal
from datetime import datetime
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape
from urllib.parse import quote
import unicodedata
import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

ROOT = Path(__file__).parent
DB = Path(os.getenv('ABSENSI_DB', str(ROOT / 'data' / 'absensi.sqlite3')))
DB.parent.mkdir(parents=True, exist_ok=True)
TZ = ZoneInfo('Asia/Jakarta')
@asynccontextmanager
async def lifespan(application):
    cleanup_expired_sessions()
    task=asyncio.create_task(retention_worker())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError): await task

app = FastAPI(title='Absensi Face Recognition', lifespan=lifespan)
lock = threading.Lock()
clients = set()
confirmations = {}

def now(): return datetime.now(TZ).isoformat(timespec='seconds')
def connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c
with connect() as c:
    c.executescript('''CREATE TABLE IF NOT EXISTS people(id INTEGER PRIMARY KEY, name TEXT NOT NULL, group_name TEXT NOT NULL, features TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, title TEXT NOT NULL, started_at TEXT NOT NULL, closed_at TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS one_open_session ON sessions((1)) WHERE closed_at IS NULL;
    CREATE TABLE IF NOT EXISTS roster(session_id INTEGER REFERENCES sessions(id), person_id INTEGER REFERENCES people(id), PRIMARY KEY(session_id,person_id));
    CREATE TABLE IF NOT EXISTS attendance(session_id INTEGER, person_id INTEGER, arrived_at TEXT NOT NULL, method TEXT NOT NULL, PRIMARY KEY(session_id,person_id), FOREIGN KEY(session_id,person_id) REFERENCES roster(session_id,person_id));''')

    c.execute("""CREATE TABLE IF NOT EXISTS attendance_status(session_id INTEGER, person_id INTEGER, status TEXT NOT NULL CHECK(status IN ('izin','tanpa_keterangan')), updated_at TEXT NOT NULL, PRIMARY KEY(session_id,person_id), FOREIGN KEY(session_id,person_id) REFERENCES roster(session_id,person_id))""")
    if "phone" not in {r["name"] for r in c.execute("PRAGMA table_info(people)")}:
        c.execute("ALTER TABLE people ADD COLUMN phone TEXT NOT NULL DEFAULT ''")

def expires_at(started_at):
    start=datetime.fromisoformat(started_at)
    if start.tzinfo is None: start=start.replace(tzinfo=TZ)
    year=start.year+(start.month==12)
    month=1 if start.month==12 else start.month+1
    day=min(start.day,calendar.monthrange(year,month)[1])
    return start.replace(year=year,month=month,day=day)

def cleanup_expired_sessions(reference=None):
    reference=reference or datetime.now(TZ)
    with connect() as c:
        expired=[r['id'] for r in c.execute('SELECT id,started_at FROM sessions WHERE closed_at IS NOT NULL') if expires_at(r['started_at'])<=reference]
        for sid in expired:
            c.execute('DELETE FROM attendance WHERE session_id=?',(sid,))
            c.execute('DELETE FROM attendance_status WHERE session_id=?',(sid,))
            c.execute('DELETE FROM roster WHERE session_id=?',(sid,))
            c.execute('DELETE FROM sessions WHERE id=?',(sid,))
    if expired:
        # Reclaim freed database pages after the deletion transaction completes.
        with connect() as c:
            c.execute('VACUUM')
        logging.info('Retensi bulanan: %s sesi selesai dihapus.',len(expired))
    return len(expired)

async def retention_worker():
    while True:
        await asyncio.sleep(60)
        try:
            if cleanup_expired_sessions(): await broadcast()
        except Exception:
            logging.exception('Pembersihan sesi gagal; akan dicoba kembali.')

class Person(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    group_name: str = Field(default='Umum', min_length=1, max_length=80)
    phone: str = Field(default='', max_length=30)
class Session(BaseModel):
    title: str = Field(min_length=1, max_length=120)
class Frame(BaseModel):
    image: str = Field(max_length=2000000)
class StatusUpdate(BaseModel):
    status: Literal['hadir', 'izin', 'tanpa_keterangan', 'belum_hadir']

class Manual(BaseModel):
    person_id: int

def snapshot(session_id=None):
    with connect() as c:
        people = [dict(r) for r in c.execute("SELECT id,name,group_name,phone,created_at,json_array_length(features) AS samples FROM people ORDER BY name")]
        sessions = [dict(r) for r in c.execute('SELECT * FROM sessions ORDER BY id DESC')]
        selected = next((s for s in sessions if s['id']==session_id), None) if session_id else next((s for s in sessions if not s['closed_at']), sessions[0] if sessions else None)
        rows = []
        if selected:
            rows = [dict(r) for r in c.execute('''SELECT p.id,p.name,p.group_name,a.arrived_at,a.method,(a.arrived_at IS NOT NULL OR st.status IS NOT NULL) AS has_record,CASE WHEN a.arrived_at IS NOT NULL THEN 'hadir' ELSE COALESCE(st.status, CASE WHEN s.closed_at IS NULL THEN 'belum_hadir' ELSE 'tanpa_keterangan' END) END AS status FROM roster r JOIN people p ON p.id=r.person_id LEFT JOIN attendance a ON a.person_id=r.person_id AND a.session_id=r.session_id LEFT JOIN attendance_status st ON st.session_id=r.session_id AND st.person_id=r.person_id JOIN sessions s ON s.id=r.session_id WHERE r.session_id=? ORDER BY a.arrived_at DESC,p.name''',(selected['id'],))]
        return dict(people=people,sessions=sessions,selected=selected,rows=rows,model_ready=all((ROOT/'models'/f).exists() for f in ['yunet.onnx','sface.onnx']))
async def broadcast():
    for ws in list(clients):
        try: await ws.send_json({'type':'refresh'})
        except Exception: clients.discard(ws)

@app.get('/api/state')
def state(session_id: int | None = None): return snapshot(session_id)
@app.post('/api/people')
async def add_person(body: Person):
    name, group = body.name.strip(), body.group_name.strip()
    phone = re.sub(r'[\s()\-]', '', body.phone.strip())
    if phone and not re.fullmatch(r'\+?[0-9]{8,15}', phone):
        raise HTTPException(400, 'Nomor HP harus berisi 8–15 digit, boleh diawali +.')
    if not name or not group: raise HTTPException(400,'Nama dan unit / kelompok harus diisi.')
    with connect() as c:
        cur=c.execute('INSERT INTO people(name,group_name,phone,created_at) VALUES(?,?,?,?)',(name,group,phone,now()))
        pid=cur.lastrowid
        active=c.execute('SELECT id FROM sessions WHERE closed_at IS NULL').fetchone()
        if active: c.execute('INSERT INTO roster VALUES(?,?)',(active['id'],pid))
    await broadcast()
    return {'id':pid}
@app.delete('/api/people/{pid}')
async def delete_person(pid:int):
    with connect() as c:
        if not c.execute('SELECT 1 FROM people WHERE id=?',(pid,)).fetchone():
            raise HTTPException(404,'Peserta tidak ditemukan.')
        c.execute('DELETE FROM attendance WHERE person_id=?',(pid,))
        c.execute('DELETE FROM attendance_status WHERE person_id=?',(pid,))
        c.execute('DELETE FROM roster WHERE person_id=?',(pid,))
        c.execute('DELETE FROM people WHERE id=?',(pid,))
    confirmations.pop(pid,None)
    await broadcast()
    return {'ok':True}

@app.delete('/api/sessions/{sid}')
async def delete_session(sid:int):
    with connect() as c:
        if not c.execute('SELECT 1 FROM sessions WHERE id=?',(sid,)).fetchone():
            raise HTTPException(404,'Sesi tidak ditemukan.')
        c.execute('DELETE FROM attendance WHERE session_id=?',(sid,))
        c.execute('DELETE FROM attendance_status WHERE session_id=?',(sid,))
        c.execute('DELETE FROM roster WHERE session_id=?',(sid,))
        c.execute('DELETE FROM sessions WHERE id=?',(sid,))
    confirmations.clear()
    await broadcast()
    return {'ok':True}

@app.post('/api/sessions')
async def add_session(body: Session):
    title=body.title.strip()
    if not title: raise HTTPException(400,'Nama sesi harus diisi.')
    try:
        with connect() as c:
            sid=c.execute('INSERT INTO sessions(title,started_at) VALUES(?,?)',(title,now())).lastrowid
            c.execute('INSERT INTO roster SELECT ?,id FROM people',(sid,))
    except sqlite3.IntegrityError: raise HTTPException(409,'Tutup sesi aktif terlebih dahulu.')
    confirmations.clear()
    await broadcast()
    return {'id':sid}
@app.post('/api/sessions/{sid}/close')
async def close_session(sid:int):
    with connect() as c:
        if not c.execute('UPDATE sessions SET closed_at=? WHERE id=? AND closed_at IS NULL',(now(),sid)).rowcount: raise HTTPException(409,'Sesi tidak aktif.')
    confirmations.clear()
    await broadcast()
    return {'ok':True}

def record(pid,method):
    with connect() as c:
        active=c.execute('SELECT id FROM sessions WHERE closed_at IS NULL').fetchone()
        if not active: raise HTTPException(409,'Mulai sesi terlebih dahulu.')
        if not c.execute('SELECT 1 FROM roster WHERE session_id=? AND person_id=?',(active['id'],pid)).fetchone(): raise HTTPException(404,'Peserta tidak ada dalam sesi ini.')
        c.execute('DELETE FROM attendance_status WHERE session_id=? AND person_id=?',(active['id'],pid))
        cur=c.execute('INSERT OR IGNORE INTO attendance VALUES(?,?,?,?)',(active['id'],pid,now(),method))
        return bool(cur.rowcount)
@app.post('/api/attendance')
async def manual(body: Manual):
    fresh=record(body.person_id,'manual')
    await broadcast()
    return {'recorded':fresh}
@app.delete('/api/attendance/{sid}/{pid}')
async def undo(sid:int,pid:int):
    with connect() as c:
        if not c.execute('SELECT 1 FROM roster WHERE session_id=? AND person_id=?',(sid,pid)).fetchone(): raise HTTPException(404,'Peserta tidak ada dalam sesi ini.')
        c.execute('DELETE FROM attendance WHERE session_id=? AND person_id=?',(sid,pid))
        c.execute('DELETE FROM attendance_status WHERE session_id=? AND person_id=?',(sid,pid))
    confirmations.pop(pid,None)
    await broadcast()
    return {'ok':True}

@app.put('/api/attendance/{sid}/{pid}/status')
async def update_status(sid:int,pid:int,body:StatusUpdate):
    with connect() as c:
        if not c.execute('SELECT 1 FROM sessions WHERE id=? AND closed_at IS NULL',(sid,)).fetchone():
            raise HTTPException(409,'Status hanya dapat diubah pada sesi aktif.')
        if not c.execute('SELECT 1 FROM roster WHERE session_id=? AND person_id=?',(sid,pid)).fetchone():
            raise HTTPException(404,'Peserta tidak ada dalam sesi ini.')
        c.execute('DELETE FROM attendance_status WHERE session_id=? AND person_id=?',(sid,pid))
        if body.status=='hadir':
            c.execute('INSERT OR IGNORE INTO attendance VALUES(?,?,?,?)',(sid,pid,now(),'manual'))
        else:
            c.execute('DELETE FROM attendance WHERE session_id=? AND person_id=?',(sid,pid))
            if body.status!='belum_hadir':
                c.execute('INSERT INTO attendance_status VALUES(?,?,?,?)',(sid,pid,body.status,now()))
    confirmations.pop(pid,None)
    await broadcast()
    return {'ok':True}

detector=recognizer=None
def feature(image):
    global detector,recognizer
    if not snapshot()['model_ready']: raise HTTPException(503,'Model belum tersedia. Jalankan python setup_models.py.')
    try:
        raw=base64.b64decode(image.split(',')[-1], validate=True)
        frame=cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)
        if frame is None or max(frame.shape[:2])>2000: raise ValueError()
    except Exception: raise HTTPException(400,'Gambar tidak valid atau terlalu besar.')
    if detector is None:
        detector=cv2.FaceDetectorYN.create(str(ROOT/'models/yunet.onnx'),'',(640,480),0.9)
        recognizer=cv2.FaceRecognizerSF.create(str(ROOT/'models/sface.onnx'),'')
    detector.setInputSize((frame.shape[1],frame.shape[0]))
    _, faces=detector.detect(frame)
    if faces is None: raise HTTPException(422,'Wajah belum terlihat. Hadapkan wajah ke kamera.')
    if len(faces)!=1: raise HTTPException(422,'Pastikan hanya satu wajah di depan kamera.')
    face=faces[0]
    if min(face[2:4])<85: raise HTTPException(422,'Dekatkan wajah ke kamera.')
    aligned=recognizer.alignCrop(frame,face)
    if cv2.Laplacian(cv2.cvtColor(aligned,cv2.COLOR_BGR2GRAY),cv2.CV_64F).var()<30: raise HTTPException(422,'Gambar kurang tajam. Diam sejenak dan perbaiki pencahayaan.')
    v=recognizer.feature(aligned).flatten()
    return v/np.linalg.norm(v)
@app.post('/api/people/{pid}/face')
async def enroll(pid:int,body:Frame):
    with lock:
        v=feature(body.image)
        with connect() as c:
            row=c.execute('SELECT features FROM people WHERE id=?',(pid,)).fetchone()
            if not row: raise HTTPException(404,'Peserta tidak ditemukan.')
            samples=json.loads(row['features'])
            if len(samples)>=5: raise HTTPException(409,'Lima sampel sudah tersimpan.')
            samples.append(v.tolist())
            c.execute('UPDATE people SET features=? WHERE id=?',(json.dumps(samples),pid))
    await broadcast()
    return {'samples':len(samples)}
@app.delete('/api/people/{pid}/face')
async def reset_face(pid:int):
    with connect() as c: c.execute("UPDATE people SET features='[]' WHERE id=?",(pid,))
    confirmations.clear()
    await broadcast()
    return {'ok':True}
@app.post('/api/recognize')
async def recognize(body:Frame):
    with lock:
        try: v=feature(body.image)
        except HTTPException:
            confirmations.clear()
            raise
        with connect() as c:
            active=c.execute('SELECT id FROM sessions WHERE closed_at IS NULL').fetchone()
            if not active: raise HTTPException(409,'Mulai sesi terlebih dahulu.')
            people=c.execute('SELECT p.* FROM people p JOIN roster r ON r.person_id=p.id WHERE r.session_id=?',(active['id'],)).fetchall()
        ranks=[]
        for p in people:
            samples=json.loads(p['features'])
            if len(samples)<3: continue
            center=np.mean(samples,axis=0); center/=np.linalg.norm(center)
            ranks.append((float(np.dot(v,center)),p['id'],p['name']))
        ranks.sort(reverse=True)
        if not ranks or ranks[0][0]<0.5 or (len(ranks)>1 and ranks[0][0]-ranks[1][0]<0.08):
            confirmations.clear()
            return {'status':'unknown','message':'Wajah belum cocok dengan peserta terdaftar.'}
        score,pid,name=ranks[0]
        count,stamp=confirmations.get(pid,(0,0))
        count=count+1 if time.monotonic()-stamp<4 else 1
        confirmations.clear(); confirmations[pid]=(count,time.monotonic())
        if count<3: return {'status':'confirming','message':f'Memastikan {name} ({count}/3)…'}
        fresh=record(pid,'wajah')
    if fresh: await broadcast()
    return {'status':'recorded' if fresh else 'already','message':f'{name} — '+('kehadiran berhasil dicatat.' if fresh else 'sudah hadir.'),'name':name,'similarity':round(score,3)}

@app.get('/api/reports/{sid}.pdf')
def pdf(sid:int):
    data=snapshot(sid)
    if not data['selected']: raise HTTPException(404,'Sesi tidak ditemukan.')
    s=data['selected']; rows=data['rows']; present=sum(bool(r['arrived_at']) for r in rows)
    session_date=datetime.fromisoformat(s['started_at']).astimezone(TZ)
    months=['Januari','Februari','Maret','April','Mei','Juni','Juli','Agustus','September','Oktober','November','Desember']
    date_label=f'{session_date.day} {months[session_date.month-1]} {session_date.year}'
    report_title=f"Rekap Kehadiran - {s['title']} - {date_label}"
    slug=re.sub(r'[^\w-]+','-',s['title'].strip().lower(),flags=re.UNICODE).strip('-_') or 'kegiatan'
    filename=f'rekap-{slug}-{session_date:%Y-%m-%d}.pdf'
    fallback=unicodedata.normalize('NFKD',filename).encode('ascii','ignore').decode()
    buf=io.BytesIO(); styles=getSampleStyleSheet()
    doc=SimpleDocTemplate(buf,pagesize=(595,842),rightMargin=36,leftMargin=36,topMargin=40,bottomMargin=40,title=report_title,author="Absensi Face Recognition")
    story=[Paragraph('REKAP KEHADIRAN',styles['Title']),Paragraph(escape(s['title']),styles['Heading2']),Paragraph(date_label,styles['Heading2']),Paragraph(f"Status: {'Selesai' if s['closed_at'] else 'Berlangsung'}",styles['Normal']),Spacer(1,14),Paragraph(f'Peserta: {len(rows)} &nbsp; Hadir: {present} &nbsp; '+('Tidak hadir' if s['closed_at'] else 'Selain hadir')+f': {len(rows)-present}',styles['Normal']),Spacer(1,18)]
    story.insert(-1,Paragraph(f"Izin: {sum(r['status']=='izin' for r in rows)} &nbsp; Tanpa keterangan: {sum(r['status']=='tanpa_keterangan' for r in rows)}",styles['Normal']))
    table=[['No.','Nama / unit','Status','Jam datang']]
    for i,r in enumerate(sorted(rows,key=lambda r:r['name'].casefold()),1):
        status={'hadir':'Hadir','izin':'Izin','tanpa_keterangan':'Tanpa keterangan','belum_hadir':'Belum hadir'}[r['status']]
        table.append([str(i),Paragraph(escape(r['name'])+'<br/><font color="#69788c">'+escape(r['group_name'])+'</font>',styles['Normal']),Paragraph(status,styles['Normal']),r['arrived_at'][11:19] if r['arrived_at'] else '-'])
    t=Table(table,colWidths=[32,260,125,106],repeatRows=1,hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#244b7b')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f2f5f9')]),('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9)]))
    story.append(t)
    def footer(canvas,doc):
        canvas.setFont('Helvetica',9);canvas.setFillColor(colors.HexColor('#69788c'));canvas.drawString(36,22,'Absensi Face Recognition | Dicetak '+datetime.now(TZ).strftime('%d/%m/%Y %H:%M')+' WIB');canvas.drawRightString(559,22,f'Halaman {doc.page}')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return Response(buf.getvalue(),media_type='application/pdf',headers={'Content-Disposition':f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"})

@app.websocket('/ws')
async def ws_endpoint(ws:WebSocket):
    await ws.accept(); clients.add(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: pass
    finally: clients.discard(ws)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
@app.get('/')
def index(): return FileResponse(ROOT/'static/index.html')
