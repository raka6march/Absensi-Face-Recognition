const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state,selected=null,stream=null,running=false,enrolling=false,deleteTarget=null;
const statusLabels={hadir:'Hadir',izin:'Izin',tanpa_keterangan:'Tanpa keterangan',belum_hadir:'Belum hadir'};
const labels={dashboard:['Ringkasan kehadiran','Pantau peserta dan kelola sesi dalam satu tempat.'],camera:['Absensi kamera','Catat kehadiran melalui pengenalan wajah lokal.'],people:['Data peserta','Kelola identitas, unit, dan sampel wajah peserta.'],reports:['Rekap sesi','Unduh laporan kehadiran untuk setiap sesi.'],guide:['Panduan penggunaan','Langkah lengkap untuk mempelajari Absensi Face Recognition.']};
function error(e){$('#error').textContent=e.message||String(e);$('#error').hidden=false;}
async function api(url,method='GET',body){const r=await fetch(url,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});if(!r.ok){const e=await r.json().catch(()=>({detail:'Permintaan gagal.'}));throw Error(typeof e.detail==='string'?e.detail:'Periksa data yang dimasukkan.');}return r.json();}
function cameraError(selector,e){const target=$(selector);target.textContent=e.message||String(e);target.classList.add('camera-error');}
function page(name){$('#error').hidden=true;$('#error').textContent='';document.querySelectorAll('.page').forEach(e=>e.hidden=e.id!==name);document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.page===name));$('#title').textContent=labels[name][0];$('#subtitle').textContent=labels[name][1];$('#breadcrumb').textContent=document.querySelector(`[data-page="${name}"] span`).textContent;if(name!=='camera')stopCamera();$('.session-bar').hidden=name==='guide';$('#new-session').hidden=name==='guide';window.scrollTo(0,0);}
function date(s){return new Date(s).toLocaleDateString('id-ID',{day:'numeric',month:'short',year:'numeric',timeZone:'Asia/Jakarta'});}
function renderAttendance(){const rows=state.rows.filter(r=>(r.name+' '+r.group_name).toLowerCase().includes($('#search-attendance').value.toLowerCase()));const active=state.selected&&!state.selected.closed_at;$('#attendance-table').innerHTML=rows.map(r=>`<tr><td><b>${esc(r.name)}</b><small>${esc(r.group_name)}</small></td><td><span class="pill ${r.arrived_at?'':'pending'}">${statusLabels[r.status]}</span></td><td>${r.arrived_at?r.arrived_at.slice(11,16):'—'}<small>${r.method==='wajah'?'Kamera':r.method==='manual'?'Manual':''}</small></td><td>${active&&!r.arrived_at?`<button class="table-action" data-manual="${r.id}">Catat hadir</button>`:''}${r.has_record?`<button class="table-action danger-text" data-delete-record="${r.id}">Hapus catatan</button>`:(!active?'—':'')}</td></tr>`).join('')||'<tr><td colspan="4" class="empty">Belum ada peserta dalam daftar ini.<br>Tambahkan peserta dan mulai sesi.</td></tr>';}
async function refresh(){state=await api('/api/state'+(selected?'?session_id='+selected:''));if(selected&&!state.selected){selected=null;state=await api('/api/state');}const s=state.selected,total=state.rows.length,present=state.rows.filter(r=>r.arrived_at).length,pct=total?Math.round(present/total*100):0;$('#total').textContent=total;$('#present').textContent=present;$('#absent').textContent=s?.closed_at?total-present:state.rows.filter(r=>r.status==='belum_hadir').length;$('#percentage').innerHTML=pct+'<span>%</span>';$('#ring-value').textContent=pct+'%';$('#ring').style.background=`conic-gradient(var(--green) ${pct}%,#edf1ea 0%)`;$('#absent-label').textContent=s?.closed_at?'Tidak hadir':'Belum hadir';$('#absent-help').textContent=s?.closed_at?'Tidak tercatat sampai sesi ditutup':'Menunggu kedatangan peserta';$('#session-name').textContent=s?s.title:'Belum ada sesi';$('#session-date').textContent=s?date(s.started_at)+' · '+(s.closed_at?'Selesai':'Berlangsung'):'';$('#session-select').innerHTML=state.sessions.map(v=>`<option value="${v.id}">${esc(v.title)}</option>`).join('')||'<option>Belum ada sesi</option>';if(s)$('#session-select').value=s.id;$('#close-session').disabled=!s||!!s.closed_at;$('#delete-selected-session').disabled=!s;$('#new-session').disabled=state.sessions.some(v=>!v.closed_at);$('#model-status').textContent=state.model_ready?'Model siap':'Model belum tersedia';$('#people-count').textContent=state.people.length+' peserta terdaftar';$('#people-table').innerHTML=state.people.map(p=>`<tr><td><b>${esc(p.name)}</b></td><td>${esc(p.group_name)}</td><td>${esc(p.phone)||'—'}</td><td><span class="pill ${p.samples>=3?'':'pending'}">${p.samples}/5 sampel ${p.samples>=3?'· Siap':'· Belum siap'}</span></td><td><button class="table-action" data-enroll="${p.id}">Daftarkan wajah</button>${p.samples?`<button class="table-action" data-reset="${p.id}">Reset wajah</button>`:''}<button class="table-action danger-text" data-delete-person="${p.id}">Hapus peserta</button></td></tr>`).join('')||'<tr><td colspan="5" class="empty">Belum ada peserta. Mulai dengan menambahkan data peserta.</td></tr>';const old=$('#enroll-person').value;$('#enroll-person').innerHTML='<option value="">Mode absensi</option>'+state.people.map(p=>`<option value="${p.id}">${esc(p.name)} (${p.samples}/5)</option>`).join('');$('#enroll-person').value=old;enrolling=!!$('#enroll-person').value;renderReports();renderAttendance();renderMonitor();}
function image(){const video=$('#video');if(!stream||!video.videoWidth)throw Error('Nyalakan kamera dan tunggu gambarnya muncul.');const canvas=$('#capture');canvas.width=640;canvas.height=Math.round(video.videoHeight/video.videoWidth*640);canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);return canvas.toDataURL('image/jpeg',.85);}
async function recognizeLoop(){if(!running)return;try{if(!enrolling){const r=await api('/api/recognize','POST',{image:image()});$('#recognition').textContent=r.message;}}catch(e){$('#recognition').textContent=e.message;}if(running)setTimeout(recognizeLoop,650);}
function stopCamera(){running=false;stream?.getTracks().forEach(t=>t.stop());stream=null;$('#video').srcObject=null;$('#camera-placeholder').hidden=false;$('#start-camera').disabled=false;$('#stop-camera').disabled=true;}
$('#start-camera').onclick=async()=>{try{if(!state.model_ready)throw Error('Model belum tersedia. Jalankan setup_models.py terlebih dahulu.');$('#start-camera').disabled=true;$('#recognition').classList.remove('camera-error');stream=await navigator.mediaDevices.getUserMedia({video:{width:640,height:480},audio:false});$('#video').srcObject=stream;await $('#video').play();$('#camera-placeholder').hidden=true;$('#stop-camera').disabled=false;running=true;recognizeLoop();}catch(e){stopCamera();cameraError('#recognition',e.name==='NotAllowedError'?Error('Izin kamera ditolak. Izinkan kamera di pengaturan browser.'):e);}};
$('#stop-camera').onclick=stopCamera;
$('#enroll-person').onchange=()=>{enrolling=!!$('#enroll-person').value;$('#recognition').textContent=enrolling?'Mode pendaftaran — absensi otomatis dijeda.':'Mode absensi aktif.';};
$('#capture-face').onclick=async()=>{try{const pid=$('#enroll-person').value;if(!pid)throw Error('Pilih peserta untuk mendaftarkan wajah.');$('#capture-face').disabled=true;$('#enroll-status').classList.remove('camera-error');$('#enroll-status').textContent='Memproses sampel wajah…';const r=await api(`/api/people/${pid}/face`,'POST',{image:image()});$('#enroll-status').textContent=`${r.samples}/5 sampel tersimpan. ${r.samples>=3?'Wajah siap digunakan.':'Ambil minimal 3 sampel.'}`;await refresh();}catch(e){cameraError('#enroll-status',e);}finally{$('#capture-face').disabled=false;}};
$('#new-session').onclick=()=>$('#session-dialog').showModal();$('#add-person').onclick=()=>$('#person-dialog').showModal();
$('#person-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/people','POST',Object.fromEntries(new FormData(e.target)));$('#person-dialog').close();e.target.reset();await refresh();}catch(e){error(e);}};
$('#session-form').onsubmit=async e=>{e.preventDefault();try{const r=await api('/api/sessions','POST',Object.fromEntries(new FormData(e.target)));selected=r.id;$('#session-dialog').close();e.target.reset();await refresh();}catch(e){error(e);}};
$('#close-session').onclick=async()=>{if(!confirm('Tutup sesi ini? Peserta yang belum tercatat akan menjadi tanpa keterangan. Status izin tetap tersimpan.'))return;try{await api(`/api/sessions/${state.selected.id}/close`,'POST');stopCamera();await refresh();}catch(e){error(e);}};
$('#session-select').onchange=async e=>{selected=Number(e.target.value);await refresh().catch(error);};$('#search-attendance').oninput=renderAttendance;
document.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;try{if(b.dataset.page)page(b.dataset.page);if(b.dataset.go)page(b.dataset.go);if(b.hasAttribute('data-close'))b.closest('dialog').close();if(b.dataset.manual){await api('/api/attendance','POST',{person_id:Number(b.dataset.manual)});await refresh();}if(b.dataset.undo&&confirm('Batalkan catatan kehadiran peserta ini?')){await api(`/api/attendance/${state.selected.id}/${b.dataset.undo}`,'DELETE');await refresh();}if(b.dataset.enroll){page('camera');$('#enroll-person').value=b.dataset.enroll;$('#enroll-person').dispatchEvent(new Event('change'));}if(b.dataset.reset&&confirm('Hapus seluruh sampel wajah peserta ini? Data absensi tetap tersimpan.')){await api(`/api/people/${b.dataset.reset}/face`,'DELETE');await refresh();}}catch(e){error(e);}});
function connect(){const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws`);ws.onopen=()=>{$('#connection').textContent='● Terhubung langsung';$('#monitor-live').textContent='● Real time';refresh().catch(error);};ws.onmessage=()=>refresh().catch(error);ws.onclose=()=>{$('#connection').textContent='Menghubungkan ulang…';$('#monitor-live').textContent='Menghubungkan ulang…';setTimeout(connect,2000);};}
setInterval(()=>{$('#clock').textContent=new Date().toLocaleTimeString('id-ID',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Jakarta'})+' WIB';},1000);refresh().catch(error);connect();window.addEventListener('beforeunload',stopCamera);

function renderMonitor(){
  if(!state)return;
  const rows=state.rows, counts={hadir:0,izin:0,tanpa_keterangan:0,belum_hadir:0};
  rows.forEach(r=>counts[r.status]++);
  const stats=[['hadir','Hadir',counts.hadir],['tidak_hadir','Tidak hadir',counts.izin+counts.tanpa_keterangan],['izin','Izin',counts.izin],['tanpa_keterangan','Tanpa keterangan',counts.tanpa_keterangan]];
  $('#monitor-counts').innerHTML=stats.map(([k,label,n])=>`<button class="monitor-stat ${k}" data-filter-status="${k}"><strong>${n}</strong><span>${label}</span></button>`).join('')+`<div class="monitor-pending">${counts.belum_hadir} peserta belum hadir</div>`;
  $('#monitor-session').textContent=state.selected?state.selected.title:'Belum ada sesi';
  const filter=$('#monitor-filter').value,q=$('#monitor-search').value.toLowerCase();
  const filtered=rows.filter(r=>r.name.toLowerCase().includes(q)&&(filter==='all'||filter===r.status||(filter==='tidak_hadir'&&['izin','tanpa_keterangan'].includes(r.status))));
  const active=state.selected&&!state.selected.closed_at;
  $('#monitor-list').innerHTML=filtered.map(r=>`<div class="monitor-person"><div><b>${esc(r.name)}</b><small>${esc(r.group_name)}${r.arrived_at?' · '+r.arrived_at.slice(11,16)+' WIB':''}</small><span class="pill status-${r.status}">${statusLabels[r.status]}</span></div>${active?`<select data-person-status="${r.id}" aria-label="Status ${esc(r.name)}">${Object.entries(statusLabels).map(([value,label])=>`<option value="${value}" ${value===r.status?'selected':''}>${label}</option>`).join('')}</select>`:''}</div>`).join('')||'<p class="empty">Tidak ada peserta untuk status ini.</p>';
  $('#monitor-updated').textContent='Diperbarui '+new Date().toLocaleTimeString('id-ID',{timeZone:'Asia/Jakarta'})+' WIB';
}
$('#monitor-search').oninput=renderMonitor;
$('#monitor-filter').onchange=renderMonitor;
document.addEventListener('click',e=>{const b=e.target.closest('[data-filter-status]');if(b){$('#monitor-filter').value=b.dataset.filterStatus;renderMonitor();}});
document.addEventListener('change',async e=>{
  const input=e.target.closest('[data-person-status]');if(!input)return;
  input.disabled=true;
  try{await api(`/api/attendance/${state.selected.id}/${input.dataset.personStatus}/status`,'PUT',{status:input.value});await refresh();}
  catch(e){error(e);await refresh().catch(error);}
});

function requestDelete(kind,id){
  if(!state)return;
  if(kind==='person'){
    const p=state.people.find(p=>p.id===id);if(!p)return;
    deleteTarget={url:`/api/people/${id}`,kind,id};
    $('#delete-title').textContent='Hapus peserta';
    $('#delete-description').textContent=`Hapus ${p.name}? Nama, nomor HP, sampel wajah, dan seluruh catatan/status absensinya pada semua sesi akan dihapus. Jumlah peserta serta statistik sesi terkait akan berubah.`;
  }else if(kind==='session'){
    const s=state.sessions.find(s=>s.id===id);if(!s)return;
    deleteTarget={url:`/api/sessions/${id}`,kind,id,active:!s.closed_at};
    $('#delete-title').textContent='Hapus sesi';
    $('#delete-description').textContent=`Hapus sesi “${s.title}”? Seluruh daftar peserta, catatan kehadiran, status izin, dan rekap sesi ini akan dihapus. Data induk peserta tetap tersimpan.${!s.closed_at?' Sesi ini sedang aktif; kamera akan dihentikan.':''}`;
  }else{
    const r=state.rows.find(r=>r.id===id);if(!r||!r.has_record||!state.selected)return;
    deleteTarget={url:`/api/attendance/${state.selected.id}/${id}`,kind,id};
    $('#delete-title').textContent='Hapus catatan absensi';
    $('#delete-description').textContent=`Hapus catatan/status absensi ${r.name} pada “${state.selected.title}”? Data peserta dan keikutsertaannya tetap tersimpan. Status kembali menjadi ${state.selected.closed_at?'tanpa keterangan':'belum hadir'}.${!state.selected.closed_at?' Kamera yang aktif dapat mencatat ulang jika wajah terlihat.':''}`;
  }
  $('#delete-dialog').showModal();
}
$('#delete-selected-session').onclick=()=>{if(state.selected)requestDelete('session',state.selected.id);};
document.addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.deletePerson)requestDelete('person',Number(b.dataset.deletePerson));
  if(b.dataset.deleteSession)requestDelete('session',Number(b.dataset.deleteSession));
  if(b.dataset.deleteRecord)requestDelete('record',Number(b.dataset.deleteRecord));
});
$('#delete-dialog').addEventListener('close',()=>{deleteTarget=null;});
$('#delete-form').onsubmit=async e=>{
  e.preventDefault();if(!deleteTarget)return;
  const target=deleteTarget;$('#confirm-delete').disabled=true;
  try{
    await api(target.url,'DELETE');
    if(target.kind==='session'){
      if(target.active||state.selected?.id===target.id)stopCamera();
      if(selected===target.id||state.selected?.id===target.id)selected=null;
    }
    $('#delete-dialog').close();await refresh();
  }catch(e){error(e);$('#delete-dialog').close();}
  finally{$('#confirm-delete').disabled=false;}
};

const normalizeTitle=value=>value.toLocaleLowerCase('id-ID').trim().replace(/[’']/g,'').replace(/\s+/g,' ');
function renderReports(){
  if(!state)return;
  const oldTitle=$('#report-title').value;
  const names=new Map();state.sessions.forEach(s=>names.set(normalizeTitle(s.title),s.title));
  $('#report-title').innerHTML='<option value="">Semua nama sesi</option>'+[...names].sort((a,b)=>a[0].localeCompare(b[0],'id')).map(([key,label])=>`<option value="${esc(key)}">${esc(label)}</option>`).join('');
  $('#report-title').value=names.has(oldTitle)?oldTitle:'';
  const q=normalizeTitle($('#report-search').value),day=$('#report-day').value,title=$('#report-title').value,sort=$('#report-sort').value;
  const rows=state.sessions.filter(s=>{
    const name=normalizeTitle(s.title);
    return (!q||name.includes(q))&&(!title||name===title)&&(!day||name.split(/[^a-z]+/).includes(day));
  });
  rows.sort((a,b)=>sort==='name'?normalizeTitle(a.title).localeCompare(normalizeTitle(b.title),'id')||b.started_at.localeCompare(a.started_at):sort==='oldest'?a.started_at.localeCompare(b.started_at)||a.id-b.id:b.started_at.localeCompare(a.started_at)||b.id-a.id);
  $('#reports-count').textContent=`${rows.length} dari ${state.sessions.length} sesi ditampilkan`;
  $('#reports-table').innerHTML=rows.map(s=>`<tr><td><b>${esc(s.title)}</b></td><td>${date(s.started_at)}<small>${s.started_at.slice(11,16)} WIB</small></td><td><span class="pill ${s.closed_at?'':'pending'}">${s.closed_at?'Selesai':'Berlangsung'}</span></td><td><a href="/api/reports/${s.id}.pdf">↓ Unduh PDF</a></td><td><button class="table-action danger-text" data-delete-session="${s.id}">Hapus sesi</button></td></tr>`).join('')||'<tr><td colspan="5" class="empty">Tidak ada sesi yang sesuai dengan filter.</td></tr>';
}
$('#report-search').oninput=renderReports;
['#report-day','#report-title','#report-sort'].forEach(s=>$(s).onchange=renderReports);
$('#report-reset').onclick=()=>{$('#report-search').value='';$('#report-day').value='';$('#report-title').value='';$('#report-sort').value='newest';renderReports();};

document.addEventListener('click',e=>{
  const link=e.target.closest('[data-guide-jump]');if(!link)return;
  e.preventDefault();const chapter=document.getElementById('guide-'+link.dataset.guideJump);
  if(chapter){chapter.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});chapter.focus({preventScroll:true});}
});
