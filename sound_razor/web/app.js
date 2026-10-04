import {StemPlayer} from './player.js';

const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="sound-razor-token"]').content;
const player = new StemPlayer();
const colors = ['#c6e69a', '#e8ad78', '#86bfb0', '#b1a0d9', '#e3cf80', '#84aacf'];
let state = {root: null, projects: [], job: null};
let selected = null;
let audioSignature = '';
let loading = 0;
let controller = null;
let busy = false;
let editing = null;
let deleting = null;
let browseParent = '';
let youtubeJob = null;
const lookupSelections = {import: null, edit: null};
const lookupEpoch = {import: 0, edit: 0};
const coverImages = new Map();

async function coverImage(container, project) {
  const key = JSON.stringify([state.root, project.id, project.cover]);
  if (container.dataset.coverKey === key) return;
  container.dataset.coverKey = key;
  container.querySelector('img')?.remove(); container.classList.remove('has-cover');
  if (!project.cover) return;
  const root = state.root;
  if (!coverImages.has(key)) {
    coverImages.set(key, fetch(`${projectUrl(project.id)}/cover`, {headers: {'X-sound-razor-Token': token}})
      .then(r => { if (!r.ok) throw new Error('Cover unavailable'); return r.blob(); })
      .then(blob => URL.createObjectURL(blob)).catch(() => { coverImages.delete(key); return null; }));
    if (coverImages.size > 64) {
      const oldest = coverImages.keys().next().value;
      coverImages.get(oldest).then(url => { if (url) URL.revokeObjectURL(url); }); coverImages.delete(oldest);
    }
  }
  const url = await coverImages.get(key);
  if (!url || root !== state.root || container.dataset.coverKey !== key) return;
  const img = el('img'); img.alt = `${project.album || project.title} cover`; img.src = url;
  img.onerror = () => { img.remove(); container.classList.remove('has-cover'); };
  container.append(img); container.classList.add('has-cover');
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function notice(message = '') { $('notice').textContent = message; $('notice').hidden = !message; }
function time(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
async function api(path, data) {
  const response = await fetch(path, {
    method: data === undefined ? 'GET' : 'POST',
    headers: {'X-sound-razor-Token': token, ...(data === undefined ? {} : {'Content-Type': 'application/json'})},
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
function activeProject() { return state.projects.find(p => p.id === selected); }
function jobRunning() { return state.job && ['running', 'cancelling'].includes(state.job.status); }
function projectUrl(id) { return `/api/projects/${encodeURIComponent(id)}`; }
function renderLibrary() {
  $('project-count').textContent = state.projects.length;
  $('root-label').textContent = state.root || 'No folder selected';
  $('root-label').title = state.root || '';
  $('import-open').disabled = !state.root || busy;
  $('youtube-open').disabled = !state.root || busy;
  $('youtube-open').textContent = state.job?.kind === 'youtube' && jobRunning() ? 'YouTube download…' : 'Import from YouTube';
  const list = $('library'); list.replaceChildren();
  const query = $('search').value.toLowerCase();
  const projects = state.projects.filter(p => `${p.title} ${p.artist} ${p.album}`.toLowerCase().includes(query));
  for (const project of projects) {
    const button = el('button', `project-button${project.id === selected ? ' active' : ''}`);
    button.setAttribute('aria-current', project.id === selected ? 'page' : 'false');
    const icon = el('span', 'song-icon', project.stems.length ? '≋' : '♪');
    button.append(icon); coverImage(icon, project);
    const label = el('span', 'project-label');
    label.append(el('strong', '', project.title), el('small', '', `${project.artist || 'Unknown artist'} · ${project.stems.length ? `${project.stems.length} stems` : 'Not separated'}`));
    button.append(label); button.onclick = () => selectProject(project.id);
    list.append(button);
  }
  if (!projects.length) list.append(el('p', 'empty-library', query ? 'No matching songs.' : 'Your recordings will appear here.'));
}
function renderProject() {
  const project = activeProject();
  $('welcome').hidden = !!project; $('project').hidden = !project;
  $('welcome-action').textContent = state.root ? (state.projects.length ? 'Import a recording ＋' : 'Import your first recording ＋') : 'Choose a projects folder ↗';
  if (!project) { $('breadcrumb').textContent = 'Library'; return; }
  $('breadcrumb').textContent = project.title;
  $('title').textContent = project.title;
  coverImage(document.querySelector('.record-art'), project);
  $('artist').textContent = project.artist || 'Unknown artist';
  $('album').textContent = [project.album, project.year].filter(Boolean).join(' · ');
  $('source-name').textContent = project.original || 'No original file';
  $('project-warning').textContent = project.warnings.join(' ');
  $('project-warning').hidden = !project.warnings.length;
  $('separate').disabled = !project.original || jobRunning() || ($('preset').value === 'custom' && !chosenStems().length);
  $('delete-open').disabled = !!(jobRunning() && state.job.project === selected);
  $('separate').textContent = project.stems.length ? 'Separate again ↗' : 'Separate tracks ↗';
  $('separation-description').textContent = project.stems.length
    ? 'A successful rerun replaces this project’s existing stems.'
    : 'Choose your stems. We’ll take care of the separation.';
  renderJob();
  document.querySelectorAll('[data-split-guitar]').forEach(button => { button.disabled = !!jobRunning(); });
  const signature = JSON.stringify([state.root, project.id, project.stems]);
  if (signature !== audioSignature) {
    audioSignature = signature;
    loadAudio(project).catch(error => { if (error.name !== 'AbortError') notice(error.message); });
  }
}
function renderJob() {
  const job = state.job;
  const belongs = job && job.project === selected;
  $('job-panel').hidden = !belongs;
  if (!belongs) return;
  const elapsed = time((job.finished || Date.now()/1000) - job.started);
  const labels = {running: job.guitar_split ? 'Splitting guitar into lead and rhythm' : 'Separating your recording', cancelling: 'Stopping separation',
    succeeded: 'Separation complete', failed: 'Separation failed', cancelled: 'Separation cancelled'};
  $('job-status').textContent = `${labels[job.status]} · ${elapsed}${job.error ? ` — ${job.error}` : ''}`;
  $('cancel').hidden = !jobRunning(); $('cancel').disabled = job.status === 'cancelling';
  $('job-log').textContent = (job.logs || []).join('\n');
  // Backend progress includes model downloads and several inference phases.
  // Use indeterminate progress instead of presenting a misleading total percent.
  if (jobRunning()) $('job-progress').removeAttribute('value');
  else $('job-progress').value = job.status === 'succeeded' ? 100 : 0;
}
async function refresh() {
  const next = await api('/api/state');
  if (state.root !== next.root) { selected = null; audioSignature = ''; clearAudio(); }
  state = next;
  if (selected && !activeProject()) { selected = null; clearAudio(); audioSignature = ''; }
  renderLibrary(); renderProject(); renderYouTube();
}
function renderYouTube() {
  const job = state.job;
  const active = job?.kind === 'youtube' && jobRunning();
  const ours = job?.kind === 'youtube' && job.id === youtubeJob;
  $('youtube-submit').disabled = !!jobRunning();
  $('youtube-cancel').hidden = !active;
  $('youtube-cancel').disabled = job?.status === 'cancelling';
  $('youtube-progress').hidden = !active;
  for (const input of $('youtube-form').querySelectorAll('input')) input.disabled = !!active;
  if (!ours) return;
  $('youtube-status').textContent = {running:'Downloading and extracting audio…', cancelling:'Stopping download…', cancelled:'Download cancelled.', failed:'Download failed.', succeeded:'Project created.'}[job.status];
  $('youtube-error').textContent = job.error || '';
  $('youtube-details').hidden = false;
  $('youtube-log').textContent = (job.logs || []).join('\n');
  if (job.status === 'succeeded') {
    youtubeJob = null; $('youtube-dialog').close(); selectProject(job.project);
    notice('YouTube audio imported. Choose stems to separate, or use Edit details to find album artwork.');
  }
}
$('youtube-open').onclick = () => {
  if (!state.root) return openFolder();
  if (state.job?.kind === 'youtube' && jobRunning()) youtubeJob = state.job.id;
  else {
    youtubeJob = null; $('youtube-form').reset();
    $('youtube-status').textContent = ''; $('youtube-error').textContent = '';
    $('youtube-details').hidden = true;
  }
  renderYouTube(); $('youtube-dialog').showModal();
};
$('youtube-form').onsubmit = async event => {
  event.preventDefault();
  if (jobRunning()) return;
  $('youtube-submit').disabled = true; $('youtube-error').textContent = '';
  try {
    const data = fields('youtube');
    const job = await api('/api/import-youtube', {...data, url:$('youtube-url').value.trim(), root:state.root});
    youtubeJob = job.id; await refresh();
  } catch (error) { $('youtube-error').textContent = error.message; $('youtube-submit').disabled = false; }
};
$('youtube-cancel').onclick = async () => {
  try { await api('/api/cancel', {}); await refresh(); }
  catch(error) { $('youtube-error').textContent = error.message; }
};
function selectProject(id) {
  if (selected === id) return;
  selected = id; notice(); renderLibrary(); renderProject();
}
function clearAudio() {
  controller?.abort(); loading++; player.clear();
  $('tracks').replaceChildren();
  for (const id of ['play','restart','seek','reset-mix']) $(id).disabled = true;
  $('seek').value = 0; $('seek').max = 1; $('elapsed').textContent = '0:00'; $('duration').textContent = '0:00';
}
async function loadAudio(project) {
  clearAudio();
  const epoch = loading;
  controller = new AbortController();
  const signal = controller.signal;
  if (!project.stems.length) { $('audio-status').textContent = 'Separate this recording to start listening.'; return; }
  const tracks = [];
  try {
    for (const [i, stem] of project.stems.entries()) {
      $('audio-status').textContent = `Loading ${stem.name} · ${i+1} of ${project.stems.length} stems`;
      const response = await fetch(`${projectUrl(project.id)}/audio/${encodeURIComponent(stem.file)}`, {
        headers: {'X-sound-razor-Token': token}, signal,
      });
      if (!response.ok) throw new Error(`Could not load ${stem.name}. Refresh the library and try again.`);
      const encoded = await response.arrayBuffer();
      if (epoch !== loading) return;
      const buffer = await player.ensureContext().decodeAudioData(encoded);
      if (epoch !== loading) return;
      tracks.push({...stem, buffer});
    }
    player.setTracks(tracks);
    $('seek').max = player.duration;
    $('duration').textContent = time(player.duration);
    for (const id of ['play','restart','seek','reset-mix']) $(id).disabled = false;
    const durations = tracks.map(t => t.buffer.duration);
    $('audio-status').textContent = Math.max(...durations) - Math.min(...durations) > 0.25
      ? 'These stems have different durations. Shorter tracks will end first.' : '';
    renderTracks();
  } catch (error) {
    if (epoch !== loading || error.name === 'AbortError') return;
    $('audio-status').replaceChildren(el('span', '', `Audio could not load: ${error.message} `));
    const retry = el('button', 'text-button', 'Retry');
    retry.onclick = () => loadAudio(project);
    $('audio-status').append(retry);
  }
}
function drawWave(canvas, buffer, color) {
  const width = Math.max(150, canvas.clientWidth);
  const scale = window.devicePixelRatio || 1;
  canvas.width = width * scale; canvas.height = 48 * scale;
  const context = canvas.getContext('2d'); context.scale(scale, scale);
  const data = buffer.getChannelData(0);
  const columns = Math.floor(width / 3);
  const stride = Math.max(1, Math.floor(data.length / columns));
  context.fillStyle = color;
  for (let i = 0; i < columns; i++) {
    let peak = 0;
    // Real samples, bounded work even on a full-length song.
    const skip = Math.max(1, Math.floor(stride / 64));
    for (let j = i*stride; j < Math.min((i+1)*stride, data.length); j += skip) peak = Math.max(peak, Math.abs(data[j]));
    const height = Math.max(2, Math.min(46, peak * 60));
    context.fillRect(i*3, (48-height)/2, 2, height);
  }
}
function renderTracks() {
  $('tracks').replaceChildren();
  for (const [index, track] of player.tracks.entries()) {
    const row = el('div', 'track'); row.style.setProperty('--track-color', colors[index % colors.length]);
    const label = el('div'); label.append(el('div', 'track-name', track.name[0].toUpperCase()+track.name.slice(1)),
      el('div', 'track-detail', `${track.buffer.numberOfChannels === 1 ? 'MONO' : 'STEREO'} · ${time(track.buffer.duration)}`));
    if (track.file === 'guitar.wav') {
      const split = el('button', 'split-guitar');
      split.type = 'button'; split.title = 'Split into lead and rhythm';
      split.setAttribute('aria-label', 'Split into lead and rhythm');
      split.dataset.splitGuitar = ''; split.disabled = !!jobRunning();
      split.innerHTML = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h5c4 0 3-7 7-7h4M9 12c4 0 3 7 7 7h4M17 2l3 3-3 3M17 16l3 3-3 3"/></svg>';
      const projectId = selected;
      split.onclick = async () => {
        split.disabled = true;
        try { await api(`${projectUrl(projectId)}/split-guitar`, {}); notice(); await refresh(); }
        catch (error) { notice(error.message); split.disabled = !!jobRunning(); }
      };
      label.querySelector('.track-name').append(split);
    }
    const wave = el('div', 'wave-wrap'); const canvas = el('canvas'); canvas.setAttribute('aria-hidden','true');
    wave.append(canvas, el('span', 'playhead'));
    const controls = el('div', 'track-controls');
    for (const [key, text] of [['muted','M'],['solo','S']]) {
      const button = el('button', '', text);
      button.setAttribute('aria-label', `${key === 'muted' ? 'Mute' : 'Solo'} ${track.name}`);
      button.setAttribute('aria-pressed', String(track[key]));
      button.onclick = () => { track[key] = !track[key]; player.updateMix(); syncTrackControls(); };
      button.dataset.control = key; controls.append(button);
    }
    const range = el('input'); range.type = 'range'; range.min = 0; range.max = 1; range.step = .01; range.value = track.volume;
    range.setAttribute('aria-label', `${track.name} volume`);
    const value = el('span','volume-value',`${Math.round(track.volume*100)}%`);
    range.oninput = () => { track.volume = Number(range.value); value.textContent = `${Math.round(track.volume*100)}%`; player.updateMix(); };
    controls.append(range, value); row.append(label,wave,controls); $('tracks').append(row);
    drawWave(canvas, track.buffer, colors[index % colors.length]);
  }
  syncTrackControls();
}
function syncTrackControls() {
  [...$('tracks').children].forEach((row,i) => {
    const track = player.tracks[i];
    row.classList.toggle('muted', player.trackGain(track) === 0);
    row.querySelector('[data-control=muted]').setAttribute('aria-pressed', String(track.muted));
    row.querySelector('[data-control=solo]').setAttribute('aria-pressed', String(track.solo));
  });
}
function animate() {
  const position = player.position();
  $('elapsed').textContent = time(position);
  if (document.activeElement !== $('seek')) $('seek').value = position;
  $('play').textContent = player.playing ? 'Ⅱ' : '▶';
  $('play').setAttribute('aria-label', player.playing ? 'Pause' : 'Play');
  document.querySelectorAll('.playhead').forEach(p => p.style.left = `${player.duration ? position/player.duration*100 : 0}%`);
  requestAnimationFrame(animate);
}
async function browse(path) {
  const data = await api(`/api/folders?path=${encodeURIComponent(path || '')}`);
  $('folder-path').value = data.path; browseParent = data.parent;
  $('folder-children').replaceChildren();
  for (const folder of data.folders) {
    const button = el('button', '', `▸  ${folder.name}`); button.type = 'button';
    button.onclick = () => browse(folder.path).catch(e => $('folder-error').textContent = e.message);
    $('folder-children').append(button);
  }
  if (!data.folders.length) $('folder-children').append(el('p','', 'No subfolders. You can select this folder.'));
}
function openFolder() {
  $('folder-error').textContent = ''; $('create-folder').checked = false;
  $('folder-dialog').showModal();
  browse(state.root).catch(error => $('folder-error').textContent = error.message);
}
function openImport() {
  if (!state.root) return openFolder();
  $('import-form').reset(); $('file-label').textContent = 'Choose an audio file';
  resetLookup('import');
  $('import-error').textContent = ''; $('upload-progress').hidden = true;
  $('import-dialog').showModal();
}
function fields(prefix) {
  const data = Object.fromEntries(['title','artist','album','year'].map(key => [key,$(`${prefix}-${key}`).value]));
  if (lookupSelections[prefix]) data.lookup_token = lookupSelections[prefix];
  return data;
}
function upload(file, data) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const query = new URLSearchParams({...data, filename:file.name, root:state.root});
    xhr.open('POST', `/api/import?${query}`);
    xhr.setRequestHeader('X-sound-razor-Token', token);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = e => { if (e.lengthComputable) $('upload-progress').value = e.loaded/e.total*100; };
    xhr.onload = () => { try { const result=JSON.parse(xhr.responseText); xhr.status===200 ? resolve(result) : reject(new Error(result.error)); } catch (_) { reject(new Error('Import failed. Check the server terminal.')); } };
    xhr.onerror = () => reject(new Error('Could not reach the local server.'));
    xhr.send(file);
  });
}
$('folder-open').onclick = openFolder;
$('welcome-action').onclick = () => state.root ? openImport() : openFolder();
$('import-open').onclick = openImport;
$('refresh').onclick = () => refresh().catch(e => notice(e.message));
$('search').oninput = renderLibrary;
$('browse-path').onclick = () => browse($('folder-path').value).catch(e => $('folder-error').textContent=e.message);
$('folder-up').onclick = () => browse(browseParent).catch(e => $('folder-error').textContent=e.message);
document.querySelectorAll('.close-dialog').forEach(button => button.onclick = () => { if (!busy) button.closest('dialog').close(); });
$('import-dialog').addEventListener('cancel', e => { if(busy) e.preventDefault(); });
$('folder-form').onsubmit = async e => {
  e.preventDefault();
  try {
    await api('/api/library', {path:$('folder-path').value, create:$('create-folder').checked});
    $('folder-dialog').close(); notice(); await refresh();
  } catch(error) { $('folder-error').textContent=error.message; }
};
$('audio-file').onchange = () => {
  resetLookup('import');
  const file = $('audio-file').files[0]; if (!file) return;
  $('file-label').textContent = file.name;
  if (!$('import-title').value) $('import-title').value = file.name.replace(/\.[^.]+$/, '').replaceAll('_', ' ');
};
$('import-form').onsubmit = async e => {
  e.preventDefault(); const file=$('audio-file').files[0]; if (!file || busy) return;
  busy=true; $('import-submit').disabled=true; $('import-submit').textContent='Importing…';
  $('upload-progress').hidden=false; $('upload-progress').value=0; $('import-error').textContent='';
  try {
    const project=await upload(file,fields('import'));
    $('import-dialog').close(); await refresh(); selectProject(project.id); notice();
  } catch(error) { $('import-error').textContent=error.message; }
  finally { busy=false; $('import-submit').disabled=false; $('import-submit').textContent='Create project'; renderLibrary(); }
};
$('edit-open').onclick = () => {
  const p=activeProject(); if(!p)return; editing=p.id;
  resetLookup('edit');
  for(const key of ['title','artist','album','year']) $(`edit-${key}`).value=p[key]||'';
  $('edit-error').textContent=''; $('edit-dialog').showModal();
};
$('edit-form').onsubmit = async e => {
  e.preventDefault();
  try { await api(`${projectUrl(editing)}/metadata`,fields('edit')); $('edit-dialog').close(); await refresh(); }
  catch(error) { $('edit-error').textContent=error.message; }
};
function goHome(event) {
  event?.preventDefault(); selected = null; audioSignature = ''; clearAudio(); notice(); renderLibrary(); renderProject();
}
$('home').onclick = goHome;
document.querySelector('.brand').onclick = goHome;
function chosenStems() {
  return [...document.querySelectorAll('#custom-stems input:checked')].map(input => input.value);
}
$('preset').onchange = () => { $('custom-stems').hidden = $('preset').value !== 'custom'; $('other-help').hidden = $('preset').value !== 'custom'; renderProject(); };
$('custom-stems').onchange = renderProject;
$('delete-open').onclick = () => {
  const project = activeProject(); if (!project) return;
  deleting = {id: project.id, root: state.root};
  $('delete-name').textContent = project.title;
  $('delete-path').textContent = `${state.root}/${project.id}`;
  $('delete-error').textContent = ''; $('delete-dialog').showModal();
};
$('delete-form').onsubmit = async event => {
  event.preventDefault(); if (!deleting) return;
  const target = deleting; $('delete-submit').disabled = true;
  try {
    await api(`${projectUrl(target.id)}/delete`, {root: target.root});
    $('delete-dialog').close();
    if (selected === target.id && state.root === target.root) goHome();
    await refresh(); notice('Song deleted.');
  } catch (error) { $('delete-error').textContent = error.message; }
  finally { $('delete-submit').disabled = false; }
};
$('separate').onclick = async () => {
  if(!selected)return; $('separate').disabled=true;
  try { await api(`${projectUrl(selected)}/separate`,{preset:$('preset').value, stems:chosenStems()}); notice(); await refresh(); }
  catch(error) { notice(error.message); renderProject(); }
};
$('cancel').onclick = () => api('/api/cancel',{}).then(refresh).catch(e => notice(e.message));
$('play').onclick = () => player.playing ? player.pause() : player.play().catch(e => notice(e.message));
$('restart').onclick = () => player.seek(0).catch(e => notice(e.message));
$('seek').oninput = () => { $('elapsed').textContent=time(Number($('seek').value)); };
$('seek').onchange = () => player.seek(Number($('seek').value)).catch(e => notice(e.message));
$('master-volume').oninput = () => player.setVolume(Number($('master-volume').value));
$('reset-mix').onclick = () => { for(const t of player.tracks){t.muted=false;t.solo=false;t.volume=1;} player.updateMix(); renderTracks(); };
document.addEventListener('keydown', e => {
  if(e.code==='Space' && !['INPUT','TEXTAREA','SELECT','BUTTON'].includes(e.target.tagName) && !document.querySelector('dialog[open]')) {
    e.preventDefault(); $('play').click();
  }
});
window.addEventListener('resize', () => { if(player.tracks.length){renderTracks();syncTrackControls();} });
async function poll() {
  try { await refresh(); } catch(error) { notice(`Connection problem: ${error.message}`); }
  setTimeout(poll, 2000);
}
poll(); animate();

function resetLookup(prefix) {
  lookupEpoch[prefix]++;
  lookupSelections[prefix] = null;
  const panel = $(`${prefix}-lookup-results`);
  if (panel) { panel.replaceChildren(); panel.hidden = true; }
  const status = $(`${prefix}-lookup-status`);
  if (status) status.textContent = '';
  const button = $(`${prefix}-lookup`);
  if (button) button.disabled = false;
  $(`${prefix}-form`).querySelector('[type=submit]').disabled = false;
}

for (const prefix of ['import', 'edit']) {
  const form = $(`${prefix}-form`);
  const box = el('div', 'metadata-lookup');
  const find = el('button', 'secondary', 'Find details'); find.type = 'button'; find.id = `${prefix}-lookup`;
  const help = el('p', 'lookup-help', 'Search by song title, artist and album. Only this text is sent to MusicBrainz. Review a match before saving.');
  const status = el('p', 'lookup-status'); status.id = `${prefix}-lookup-status`; status.setAttribute('role', 'status');
  const results = el('div', 'lookup-results'); results.id = `${prefix}-lookup-results`; results.hidden = true;
  box.append(find, help, status, results);
  form.insertBefore(box, form.querySelector('[type=submit]'));
  for (const key of ['title','artist','album']) $(`${prefix}-${key}`).addEventListener('input', () => resetLookup(prefix));
  form.closest('dialog').addEventListener('close', () => resetLookup(prefix));
  find.onclick = async () => {
    resetLookup(prefix);
    const epoch = ++lookupEpoch[prefix];
    find.disabled = true; status.textContent = 'Searching MusicBrainz…';
    try {
      const data = await api('/api/metadata/search', fields(prefix));
      if (epoch !== lookupEpoch[prefix]) return;
      status.textContent = data.matches.length ? 'Choose a match to fill the fields. You can edit them before saving.' : 'No matches. Check the title and artist, or try removing the album.';
      results.hidden = !data.matches.length;
      for (const match of data.matches) {
        const card = el('div', 'lookup-match');
        card.append(el('strong', '', `${match.title} — ${match.artist}`));
        card.append(el('p', '', [match.album, match.year, match.type, match.edition, match.recording_note].filter(Boolean).join(' · ')));
        const source = el('a', 'lookup-source', 'MusicBrainz');
        source.href = `https://musicbrainz.org/release/${encodeURIComponent(match.release_id)}`; source.target = '_blank'; source.rel = 'noopener noreferrer';
        const choose = el('button', 'text-button', 'Use this match'); choose.type = 'button';
        choose.onclick = async () => {
          const selectionEpoch = ++lookupEpoch[prefix];
          results.querySelectorAll('button').forEach(b => b.disabled = true);
          find.disabled = true; form.querySelector('[type=submit]').disabled = true;
          status.textContent = 'Fetching album cover…';
          try {
            const prepared = await api('/api/metadata/prepare', {token: match.token});
            if (selectionEpoch !== lookupEpoch[prefix]) return;
            for (const key of ['title','artist','album','year']) if (prepared.fields[key]) $(`${prefix}-${key}`).value = prepared.fields[key];
            lookupSelections[prefix] = prepared.token;
            results.replaceChildren(); results.hidden = false;
            results.append(el('p', '', `${match.album} selected. Save to keep these details${prepared.has_cover ? ' and cover' : ''}.`));
            if (prepared.has_cover) {
              const response = await fetch(`/api/metadata/cover/${prepared.token}`, {headers: {'X-sound-razor-Token': token}});
              if (!response.ok) throw new Error('Cover preview unavailable. You can still save the selected details.');
              const url = URL.createObjectURL(await response.blob());
              if (selectionEpoch !== lookupEpoch[prefix]) { URL.revokeObjectURL(url); return; }
              const img = el('img', 'lookup-cover'); img.alt = `${match.album} cover`;
              img.onload = img.onerror = () => URL.revokeObjectURL(url); img.src = url; results.prepend(img);
            }
            status.textContent = prepared.warning || 'Details ready to save.';
          } catch (error) {
            if (selectionEpoch === lookupEpoch[prefix]) status.textContent = error.message;
          } finally {
            if (selectionEpoch === lookupEpoch[prefix]) {
              find.disabled = false; form.querySelector('[type=submit]').disabled = false;
              results.querySelectorAll('button').forEach(b => b.disabled = false);
            }
          }
        };
        card.append(source, choose); results.append(card);
      }
    } catch (error) { if (epoch === lookupEpoch[prefix]) status.textContent = error.message; }
    finally { if (epoch === lookupEpoch[prefix]) find.disabled = false; }
  };
}
