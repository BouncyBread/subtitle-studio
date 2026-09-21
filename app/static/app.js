'use strict';
const $ = id => document.getElementById(id);
let chosen = null, selected = null, currentJob = null, segments = [], dirty = false, page = 0, polling = false, submitting = false;
const pageSize = 30;
const labels = {queued:'Queued',audio:'Reading audio',model:'Loading model',transcribing:'Processing',done:'Ready',error:'Needs attention',cancelled:'Cancelled'};
const done = stage => ['done','error','cancelled'].includes(stage);
function showError(error) { $('alert').textContent = error.message || String(error); $('alert').hidden = false; }
async function api(path, options={}) {
  const response = await fetch(path, options);
  if(!response.ok) { const body = await response.json().catch(()=>({})); throw new Error(typeof body.detail === 'string' ? body.detail : 'Please check the settings and try again.'); }
  return response.json();
}
function settings() { return {model:$('model').value,language:$('language').value,translate:$('translate').checked,track:Number($('track').value),width:Number($('width').value),prompt:$('prompt').value,buffer_seconds:Number($('buffer').value)}; }
function remember() { try {localStorage.setItem('subtitle-settings',JSON.stringify(settings()));} catch {} }
function modelHelp() {
  const messages = {large:'Highest accuracy option here. About 3 GB to download.',turbo:'Faster transcription with a small accuracy tradeoff. About 1.6 GB.',small:'Less memory, lower accuracy. About 500 MB to download.',tiny:'Fastest way to test your setup. Lower accuracy. About 75 MB.'};
  $('model-help').textContent = messages[$('model').value];
  $('translation-help').textContent = $('translate').checked && $('model').value === 'turbo' ? 'English translation automatically uses Large v3. Turbo does not support translation.' : 'Uses Whisper directly. No account or separate translator needed.';
  remember();
}
try { const s=JSON.parse(localStorage.getItem('subtitle-settings') || 'null'); if(s){ for(const k of ['model','language','prompt','width']) if(s[k]!==undefined) $(k).value=s[k]; $('buffer').value=s.buffer_seconds||60; $('translate').checked=s.translate; } } catch {}
modelHelp();
$('settings-form').addEventListener('change',modelHelp);
let selectionVersion=0;
function uploadForTracks(file, version) {
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('POST','/api/media/upload');
    xhr.upload.onprogress=e=>{if(version===selectionVersion&&e.lengthComputable)$('track-help').textContent=`Copying locally to inspect audio tracks… ${Math.round(e.loaded/e.total*100)}%`;};
    xhr.onload=()=>{let body;try{body=JSON.parse(xhr.responseText);}catch{return reject(new Error('Unexpected server response.'));}xhr.status<300?resolve(body):reject(new Error(typeof body.detail==='string'?body.detail:'Could not read the file.'));};
    xhr.onerror=()=>reject(new Error('Copy failed. Choose the file again.'));
    const form=new FormData();form.append('file',file);xhr.send(form);
  });
}
async function chooseFile(value) {
  const version=++selectionVersion;
  chosen=null;$('generate').disabled=true;$('watch').disabled=true;
  $('file-name').textContent=value.name;
  $('file-meta').textContent=value.url?'TorBox · shared local download cache':value.path?'Opened directly from your Mac · no copy needed':`${(value.file.size/1024/1024).toFixed(1)} MB · copied once for inspection and processing`;
  $('choose').textContent='Change file…';$('alert').hidden=true;
  $('track').disabled=true;$('track').replaceChildren(new Option('Reading audio tracks…','0'));
  $('track-help').textContent='Inspecting the selected file…';
  try {
    const result=value.url?await api('/api/media/remote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:value.url})}):value.path?await api('/api/media/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:value.path})}):await uploadForTracks(value.file,version);
    if(version!==selectionVersion)return;
    if(result.upload_id)value.upload_id=result.upload_id;
    if(result.remote_id){value.remote_id=result.remote_id;delete value.url;}
    $('track').replaceChildren();
    for(const t of result.tracks){
      const label=[`${t.index+1}. ${t.language_name}`,t.title,t.codec.toUpperCase(),t.channels?`${t.channels} ch`:'',t.default?'Default':''].filter(Boolean).join(' · ');
      $('track').append(new Option(label,String(t.index)));
    }
    $('track').value=String((result.tracks.find(t=>t.default)||result.tracks[0]).index);
    $('track').disabled=false;
    $('track-help').textContent=`${result.tracks.length} audio track${result.tracks.length===1?'':'s'} found. This selection controls both subtitles and live-player audio.`;
    chosen=value;$('generate').disabled=submitting;$('watch').disabled=submitting;
    refreshCleanup(true).catch(showError);
  }catch(e){if(version===selectionVersion){showError(e);$('track').replaceChildren(new Option('Could not read audio tracks','0'));$('track-help').textContent=value.url?'Paste a fresh direct video link to retry.':'Choose the file again to retry.';}}
}
$('load-remote').onclick=async()=>{const url=$('remote-url').value.trim();if(!url)return;$('remote-url').value='';$('load-remote').disabled=true;try{await chooseFile({url,name:'TorBox video'});}finally{$('load-remote').disabled=false;}};
$('choose').onclick=async()=>{ $('choose').disabled=true; try {const value=await api('/api/pick',{method:'POST'}); if(value.path) chooseFile({path:value.path,name:value.path.split('/').pop()});}catch(e){showError(e);}finally{$('choose').disabled=false;} };
$('browse').onclick=()=>$('file').click();
$('file').onchange=()=>{if($('file').files[0])chooseFile({file:$('file').files[0],name:$('file').files[0].name});};
for(const event of ['dragenter','dragover']) $('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.add('dragging');});
for(const event of ['dragleave','drop']) $('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.remove('dragging');});
$('dropzone').addEventListener('drop',e=>{const f=e.dataTransfer.files[0];if(f)chooseFile({file:f,name:f.name});});
async function startJob(streaming) {
  if(!chosen||submitting)return;
  if(!$('settings-form').reportValidity())return;
  if(dirty && !await save())return;
  submitting=true;$('generate').disabled=true;$('watch').disabled=true;
  $('generate').textContent='Adding file…';$('alert').hidden=true;
  try {
    const s={...settings(),streaming};const submittedFile=chosen;remember();
    const job=submittedFile.remote_id?await api('/api/jobs/remote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({remote_id:submittedFile.remote_id,settings:s})}):submittedFile.path?await api('/api/jobs/path',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:submittedFile.path,settings:s})}):await api('/api/jobs/prepared',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({upload_id:submittedFile.upload_id,settings:s})});
    if(chosen===submittedFile&&(submittedFile.upload_id||submittedFile.remote_id)){chosen=null;$('track').disabled=true;$('track-help').textContent='File added. Choose it again to create another job.';}
    await select(job.id);await refreshHistory();
    if(streaming) await api(`/api/jobs/${job.id}/player`,{method:'POST'});
  }catch(e){showError(e);}
  finally{submitting=false;$('generate').disabled=!chosen;$('watch').disabled=!chosen;$('generate').textContent='Generate subtitles ↗';}
}
$('settings-form').onsubmit=e=>{e.preventDefault();startJob(false);};
$('watch').onclick=()=>startJob(true);
$('reopen-player').onclick=async()=>{try{await api(`/api/jobs/${selected}/player`,{method:'POST'});await select(selected);}catch(e){showError(e);}};
function markDirty(){dirty=true;$('save').disabled=false;$('save-state').textContent='Unsaved edits';document.querySelectorAll('.download').forEach(a=>a.setAttribute('aria-disabled','true'));}
async function save(){if(!dirty)return true;try{await api(`/api/jobs/${selected}/subtitles`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({segments})});dirty=false;$('save').disabled=true;$('save-state').textContent='All edits saved';document.querySelectorAll('.download').forEach(a=>a.removeAttribute('aria-disabled'));return true;}catch(e){showError(e);return false;}}
$('save').onclick=save;
for(const kind of ['srt','vtt','txt']) $(kind).onclick=async e=>{if(dirty){e.preventDefault();if(await save()) window.location.assign($(kind).href);}};
function renderCues(){const host=$('cues');host.replaceChildren();segments.slice(page*pageSize,(page+1)*pageSize).forEach((segment,index)=>{const number=page*pageSize+index+1;const row=document.createElement('div');row.className='cue';const times=document.createElement('div');times.className='times';for(const key of ['start','end']){const input=document.createElement('input');input.type='number';input.step='0.001';input.min='0';input.value=segment[key].toFixed(3);input.setAttribute('aria-label',`Subtitle ${number} ${key} in seconds`);input.oninput=()=>{segment[key]=input.value===''?null:Number(input.value);markDirty();};times.append(input);}const text=document.createElement('textarea');text.value=segment.text;text.rows=2;text.setAttribute('aria-label',`Subtitle ${number} text`);text.oninput=()=>{segment.text=text.value;markDirty();};row.append(times,text);host.append(row);});const pages=Math.max(1,Math.ceil(segments.length/pageSize));$('page-label').textContent=`Page ${page+1} of ${pages}`;$('previous').disabled=page===0;$('next').disabled=page+1>=pages;$('pagination').hidden=segments.length<=pageSize;}
$('previous').onclick=()=>{page--;renderCues();};$('next').onclick=()=>{page++;renderCues();};
function duration(seconds) {
  const n = Math.max(0, Math.round(seconds));
  const hours = Math.floor(n / 3600), minutes = Math.floor(n % 3600 / 60), secs = n % 60;
  return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${secs}s` : `${secs}s`;
}
function renderProgress(job) {
  const metrics = job.metrics;
  const measuring = job.stage === 'transcribing' && metrics;
  $('progress').hidden = done(job.stage);
  if (measuring) {
    $('progress').value = metrics.percent;
    $('progress-percent').textContent = `${metrics.percent.toFixed(1)}% of audio processed`;
    const eta = metrics.eta_seconds;
    $('remaining').textContent = metrics.percent >= 100 ? 'Finishing subtitles…' : eta === null ? 'Estimating time remaining…' : `About ${duration(eta)} remaining`;
    $('elapsed').textContent = `${duration(metrics.processed_seconds)} of ${duration(metrics.total_seconds)} audio · ${duration(metrics.elapsed_seconds)} processing · Estimate may change with dialogue complexity.`;
  } else {
    $('progress').removeAttribute('value');
    $('progress-percent').textContent = labels[job.stage] || 'Preparing…';
    $('remaining').textContent = done(job.stage) ? '' : job.stage === 'transcribing' ? 'Waiting for the first audio chunk…' : 'Time estimate starts during transcription';
    $('elapsed').textContent = done(job.stage) ? '' : `Added ${Math.max(0,Math.floor((Date.now()-Date.parse(job.created))/60000))} min ago · You can leave this page open while it works.`;
  }
}
async function refreshLog() {
  if (!selected || !$('activity').open) return;
  const id = selected;
  const log = await api(`/api/jobs/${id}/log`);
  if (id !== selected) return;
  const content = log.lines.join('\n') || 'Waiting for the worker to start…';
  if ($('live-log').textContent !== content) {
    $('live-log').textContent = content;
    if ($('follow-log').checked) $('live-log').scrollTop = $('live-log').scrollHeight;
  }
  const age = log.seconds_since_update;
  $('log-status').textContent = currentJob && done(currentJob.stage) ? 'Processing stopped · saved log' : age === null ? 'Waiting for log output…' : `Last output ${duration(age)} ago · Checked just now${age > 30 ? ' · The model may still be working on the current chunk.' : ''}`;
}
$('activity').addEventListener('toggle', () => { if ($('activity').open) refreshLog().catch(showError); });
function bytesLabel(n) {
  if(n < 1024) return `${n} B`;
  if(n < 1024*1024) return `${(n/1024).toFixed(1)} KB`;
  if(n < 1024*1024*1024) return `${(n/1024/1024).toFixed(1)} MB`;
  return `${(n/1024/1024/1024).toFixed(2)} GB`;
}
let cleanupBusy = false, lastCleanupCheck = 0;
async function refreshCleanup(force=false) {
  if(cleanupBusy || (!force && Date.now()-lastCleanupCheck < 10000))return;
  const data=await api('/api/cleanup');lastCleanupCheck=Date.now();
  $('cleanup-size').textContent=data.files?`${bytesLabel(data.bytes)} in ${data.files} unused media file${data.files===1?'':'s'}`:'No unused media copies to remove.';
  if(data.skipped_jobs)$('cleanup-size').textContent+=` ${data.skipped_jobs} active job/player${data.skipped_jobs===1?'':'s'} protected.`;
}
$('cleanup').onclick=async()=>{
  if(cleanupBusy)return;
  cleanupBusy=true;$('cleanup').disabled=true;$('cleanup-result').textContent='Cleaning up…';
  try {
    const data=await api('/api/cleanup',{method:'POST'});
    if(chosen?.upload_id||chosen?.remote_id){chosen=null;selectionVersion++;$('generate').disabled=true;$('watch').disabled=true;$('track').disabled=true;$('track-help').textContent='Prepared copy cleaned up. Choose the original file again.';}
    $('cleanup-result').textContent=data.files?`Freed ${bytesLabel(data.bytes)}. Subtitles and history kept.`:'Nothing unused to remove.';
    if(data.skipped_jobs)$('cleanup-result').textContent+=' Active jobs and players were skipped.';
    if(data.errors)$('cleanup-result').textContent+=` ${data.errors} file(s) could not be removed.`;
    if(selected){const job=await api(`/api/jobs/${selected}`);if(job.id===selected){currentJob=job;renderPlayer(job);}}
  }catch(e){showError(e);$('cleanup-result').textContent='Cleanup could not finish. You can try again.';}
  finally{cleanupBusy=false;$('cleanup').disabled=false;refreshCleanup(true).catch(showError);}
};
function renderPlayer(job) {
  $('download-status').hidden=!job.remote;
  if(job.remote){const d=job.download;$('download-status').textContent=d?(d.error||`TorBox cache: ${bytesLabel(d.bytes)} of ${bytesLabel(d.total)} fetched · shared by audio processing and playback`):'TorBox session ended. Paste the link again to watch; saved subtitles remain available.';}
  $('player-panel').hidden=!job.settings.streaming;
  if(!job.settings.streaming)return;
  const p=job.player||{}, stream=job.stream||{};
  if(job.media_available===false && !p.running){
    $('reopen-player').disabled=true;$('reopen-player').textContent='Choose original to watch';
    $('player-status').textContent='The media copy was cleaned up or the source was moved. Your subtitles are still available below.';
    return;
  }
  $('reopen-player').disabled=['error','cancelled'].includes(job.stage);
  $('reopen-player').textContent=p.running?'Player is open':'Open player ↗';
  $('reopen-player').disabled=$('reopen-player').disabled||p.running;
  $('player-status').textContent=p.running?(p.failed?'Generation stopped. Check the processing log.':p.buffering?`Building subtitles · ${duration(p.buffer_seconds||0)} buffered`:p.user_paused?`Paused by you · subtitles ready through ${duration(stream.covered_until||0)}`:stream.complete?'All subtitles ready. Enjoy your video.':`Playing · ${duration(p.buffer_seconds||0)} of subtitles ahead`):`Subtitles ready through ${duration(stream.covered_until||0)}. Open the player to watch.`;
}
function renderJob(job) {
  currentJob=job;
  $('empty').hidden=true; $('job-view').hidden=false;
  $('job-name').textContent=job.name;
  $('job-badge').textContent=labels[job.stage]||job.stage;
  $('job-badge').className='badge'+(job.stage==='error'?' error':'');
  $('progress-panel').hidden=job.stage==='done'; $('results').hidden=job.stage!=='done';
  $('cancel').hidden=done(job.stage); $('progress-message').textContent=job.message;
  document.querySelectorAll('[data-stage]').forEach(el=>el.classList.toggle('current',el.dataset.stage===job.stage));
  renderProgress(job);
  renderPlayer(job);
  if(job.stage==='done'&&job.result){
    segments=job.result.segments;page=0;dirty=false;$('save').disabled=true;$('save-state').textContent='';
    $('result-description').textContent=`${segments.length} subtitles · ${job.result.translated?'English output':'Original language'} · detected ${job.result.language}`;
    for(const kind of ['srt','vtt','txt']) $(kind).href=`/api/jobs/${job.id}/download/${kind}`;
    renderCues();
    if(!segments.length)$('result-description').textContent='No speech detected. Try another audio track or set the spoken language.';
  }
}
async function select(id){if(dirty&&!await save())return;selected=id;$('live-log').textContent='Loading log…';const job=await api(`/api/jobs/${id}`);if(selected===id){renderJob(job);await refreshLog();}}
$('cancel').onclick=async()=>{try{await api(`/api/jobs/${selected}/cancel`,{method:'POST'});await select(selected);await refreshHistory();}catch(e){showError(e);}};
async function refreshHistory(){const jobs=await api('/api/jobs');$('job-count').textContent=jobs.length?`${jobs.length} file${jobs.length===1?'':'s'}`:'Ready when you are';$('no-history').hidden=jobs.length>0;const host=$('history-list');host.replaceChildren();jobs.forEach(job=>{const button=document.createElement('button');button.className='history-item';button.setAttribute('aria-current',String(job.id===selected));const name=document.createElement('span');name.textContent=job.name;const status=document.createElement('span');status.className='badge'+(job.stage==='error'?' error':'');status.textContent=labels[job.stage]||job.stage;button.append(name,status);button.onclick=async()=>{try{await select(job.id);await refreshHistory();}catch(e){showError(e);}};host.append(button);});return jobs;}
async function poll(){if(polling)return;polling=true;try{if(selected&&currentJob&&(!done(currentJob.stage)||currentJob.settings.streaming)){const id=selected;const job=await api(`/api/jobs/${id}`);if(id===selected){if(done(currentJob.stage)&&done(job.stage)){currentJob=job;renderPlayer(job);}else renderJob(job);}}await refreshLog();await refreshHistory();await refreshCleanup();}catch(e){showError(new Error('Connection lost. Keep the launcher window open, or start Subtitle Studio again.'));}finally{polling=false;}}
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
(async()=>{try{const status=await api('/api/status');if(!status.ffmpeg)showError(new Error('FFmpeg is missing. Install it with: brew install ffmpeg'));await refreshCleanup(true);const jobs=await refreshHistory();if(jobs.length)await select(jobs[0].id);const initialFile=new URLSearchParams(location.hash.slice(1)).get('file');if(initialFile){history.replaceState(null,'',location.pathname);await chooseFile({path:initialFile,name:initialFile.split('/').pop()});}}catch(e){showError(e);}setInterval(poll,2500);})();
