/**
 * Editor Module for Subtitulador
 * Handles edit panel, toolbar actions, keyboard shortcuts, and main coordination
 */

import { state, history, cutState, pushUndo, undo, redo, resetState, enterCutMode, exitCutMode, initTheme, toggleTheme, toggleVideoCompact } from './state.js';
import { transcribeVideo, getVideoURL, burnSubtitles, getSRTURL, saveSegments } from './api.js';
import { renderTimeline, highlightBlocks, initTimeline, updatePlayhead, saveSegmentsDebounced } from './timeline.js';

// ===== DOM REFERENCES =====
let $;
let videoPlayer, track, playhead, timeline;
let setEditorEnabled;

// ===== INITIALIZATION =====
export async function initEditor() {
    $ = id => document.getElementById(id);
    
    videoPlayer = $('videoPlayer');
    track = $('timelineTrack');
    playhead = $('playhead');
    timeline = $('timeline');
    
    // Initialize timeline module
    initTimeline({
        $,
        videoPlayer,
        track,
        playhead,
        timeline,
        renderEditPanel,
        updateOverlay,
        setEditorEnabled: setEditorEnabledFn
    });
    
    initTheme();
    initEventListeners();
    
    setEditorEnabledFn(false);
}

function setEditorEnabledFn(enabled) {
    const ids = ['btnPlay', 'btnSplit', 'btnDelete', 'btnAdd', 'btnZoomIn', 'btnZoomOut', 'btnExportAll',
        'btnUndo', 'btnRedo', 'btnNewVideo', 'btnDownloadSrt', 'btnBurn'];
    ids.forEach(id => {
        const el = $(id);
        if (el) el.disabled = !enabled;
    });
}

// ===== EVENT LISTENERS =====
function initEventListeners() {
    // Theme toggle
    $('themeToggle').onclick = toggleTheme;
    $('videoCompactToggle').onclick = toggleVideoCompact;
    
    // Transcribe
    $('btnTranscribe').onclick = handleTranscribe;
    
    // Toolbar
    $('btnPlay').onclick = () => videoPlayer.paused ? videoPlayer.play() : videoPlayer.pause();
    $('btnSplit').onclick = handleSplit;
    $('btnDelete').onclick = deleteActive;
    $('btnAdd').onclick = addSegment;
    $('btnZoomIn').onclick = () => { state.pxPerSec = Math.min(state.pxPerSec * 1.5, 300); renderTimeline(); };
    $('btnZoomOut').onclick = () => { state.pxPerSec = Math.max(state.pxPerSec / 1.5, 15); renderTimeline(); };
    $('btnUndo').onclick = () => { if (undo()) renderTimeline(); renderEditPanel(); };
    $('btnRedo').onclick = () => { if (redo()) renderTimeline(); renderEditPanel(); };
    
    // Export
    $('btnNewVideo').onclick = handleNewVideo;
    $('btnDownloadSrt').onclick = () => { if (state.sessionId) window.location.href = getSRTURL(state.sessionId); };
    $('btnBurn').onclick = handleBurn;
    $('btnExportAll').onclick = async () => {
        $('btnDownloadSrt').click();
        await new Promise(r => setTimeout(r, 500));
        $('btnBurn').click();
    };
    
    // Video events
    videoPlayer.ontimeupdate = updatePlayhead;
    
    // Timeline click to seek
    timeline.onmousedown = e => {
        if (e.target.closest('.timeline-block')) return;
        const rect = timeline.getBoundingClientRect();
        const x = e.clientX - rect.left + timeline.scrollLeft;
        videoPlayer.currentTime = Math.max(0, Math.min(x / state.pxPerSec, state.videoDuration));
    };
    
    // Keyboard shortcuts
    document.addEventListener('keydown', handleKeydown);
    
    // Upload handlers
    initUploadHandlers();
}

function initUploadHandlers() {
    const dropzone = $('dropzone');
    const fileInput = $('fileInput');
    
    dropzone.onclick = () => fileInput.click();
    
    fileInput.onchange = e => {
        if (e.target.files[0]) pickFile(e.target.files[0]);
    };
    
    dropzone.ondragover = e => { e.preventDefault(); dropzone.classList.add('dragover'); };
    dropzone.ondragleave = () => dropzone.classList.remove('dragover');
    dropzone.ondrop = e => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        if (e.dataTransfer.files[0]) pickFile(e.dataTransfer.files[0]);
    };
    
    $('configToggle').onclick = () => $('configPanel').classList.toggle('open');
    $('noiseThreshold').oninput = e => $('noiseVal').textContent = e.target.value + ' dB';
    $('minDuration').oninput = e => $('minDurVal').textContent = parseFloat(e.target.value).toFixed(1) + ' s';
    $('maxRepeats').oninput = e => $('maxRepVal').textContent = e.target.value;
}

function pickFile(f) {
    state.selectedFile = f;
    $('filename').textContent = f.name + ' (' + (f.size / 1024 / 1024).toFixed(1) + ' MB)';
    dropzone.classList.add('has-file');
    $('btnTranscribe').disabled = false;
}

// ===== TRANSCRIBE =====
async function handleTranscribe() {
    if (!state.selectedFile) return;
    
    const btn = $('btnTranscribe');
    const fd = new FormData();
    fd.append('video', state.selectedFile);
    fd.append('language', $('language').value);
    fd.append('target_language', $('targetLanguage').value);
    fd.append('model', $('modelSelect').value);
    fd.append('noise_threshold', $('noiseThreshold').value + 'dB');
    fd.append('min_duration', $('minDuration').value);
    fd.append('max_repeats', $('maxRepeats').value);
    
    try {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Procesando...';
        $('error').style.display = 'none';
        
        setProgress(true, 'Extrayendo audio...');
        const stages = ['Extrayendo audio...', 'Transcribiendo...', 'Traduciendo...', 'Finalizando...'];
        let stageIdx = 0;
        const stageTimer = setInterval(() => {
            stageIdx = Math.min(stageIdx + 1, stages.length - 1);
            $('progressText').textContent = stages[stageIdx];
        }, 1300);
        
        const data = await transcribeVideo(fd);
        clearInterval(stageTimer);
        
        state.sessionId = data.session_id;
        state.segments = data.segments || [];
        state.activeIdx = -1;
        
        setProgress(true, 'Listo!');
        $('progressBar').style.width = '100%';
        
        setTimeout(() => {
            setProgress(false);
            $('uploadArea').hidden = true;
            $('editor').classList.add('active');
            history.undo = [];
            history.redo = [];
            setEditorEnabledFn(true);
            openEditor();
        }, 300);
        
    } catch (err) {
        $('errorText').textContent = err.message || String(err);
        $('error').style.display = 'block';
        btn.disabled = false;
        btn.textContent = 'Transcribir';
        setProgress(false);
    }
}

function openEditor() {
    if (!state.sessionId) return;
    
    state.activeIdx = -1;
    setEditorEnabledFn(true);
    
    videoPlayer.src = getVideoURL(state.sessionId);
    videoPlayer.onloadedmetadata = () => {
        state.videoDuration = videoPlayer.duration;
        renderTimeline();
    };
}

// ===== SPLIT / CUT MODE =====
function handleSplit() {
    // MÉTODO NUEVO: Usar tiempo del playhead SIEMPRE
    if (state.activeIdx < 0 || state.activeIdx >= state.segments.length) return;
    
    const seg = state.segments[state.activeIdx];
    const t = videoPlayer.currentTime;
    
    // Verificar que el playhead esté dentro del segmento
    if (t <= seg.start || t >= seg.end) {
        alert('Posicioná la línea roja (playhead) DENTRO del subtítulo seleccionado');
        return;
    }
    
    // Siempre preguntar confirmación
    if (!confirm(`¿Cortar en ${fmtTime(t)}?\n\nEl subtítulo se dividirá en esa posición.`)) {
        return;
    }
    
    pushUndo();
    
    // Dividir en la posición exacta del playhead
    const words = seg.text.split(/\s+/).filter(w => w.length > 0);
    const totalDuration = seg.end - seg.start;
    const firstDuration = t - seg.start;
    const proportion = firstDuration / totalDuration;
    const wordsInFirst = Math.max(1, Math.min(words.length - 1, Math.round(words.length * proportion)));
    
    const firstText = words.slice(0, wordsInFirst).join(' ');
    const secondText = words.slice(wordsInFirst).join(' ');
    
    state.segments.splice(state.activeIdx, 1,
        { start: seg.start, end: t, text: firstText },
        { start: t, end: seg.end, text: secondText }
    );
    
    exitCutMode();
    
    const btn = $('btnSplit');
    btn.style.background = '';
    btn.style.color = '';
    btn.innerHTML = '✂';
    
    setEditorEnabledFn(true);
    renderTimeline();
    renderEditPanel();
}

// ===== OTHER ACTIONS =====
function deleteActive() {
    if (state.activeIdx < 0) return;
    pushUndo();
    state.segments.splice(state.activeIdx, 1);
    state.activeIdx = Math.min(state.activeIdx, state.segments.length - 1);
    renderTimeline();
    renderEditPanel();
}

function addSegment() {
    pushUndo();
    const t = videoPlayer.currentTime;
    state.segments.push({ start: t, end: t + 2, text: '' });
    state.activeIdx = state.segments.length - 1;
    renderTimeline();
    renderEditPanel();
}

function handleNewVideo() {
    $('editor').classList.remove('active');
    $('uploadArea').hidden = false;
    videoPlayer.pause();
    videoPlayer.src = '';
    
    resetState();
    
    $('btnTranscribe').disabled = true;
    $('btnTranscribe').textContent = 'Transcribir';
    $('error').style.display = 'none';
    
    setEditorEnabledFn(false);
    setProgress(false);
    
    $('filename').textContent = '';
    $('fileInput').value = '';
    $('dropzone').classList.remove('has-file');
}

async function handleBurn() {
    if (!state.sessionId) return;
    
    const btn = $('btnBurn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';
    
    try {
        const blob = await burnSubtitles(state.sessionId);
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'subtitled.mp4';
        a.click();
        URL.revokeObjectURL(a.href);
    } catch (err) {
        alert(err.message);
    }
    
    btn.disabled = false;
    btn.textContent = 'Quemar';
}

// ===== EDIT PANEL =====
export function renderEditPanel() {
    const container = $('editSegment');
    
    if (!Array.isArray(state.segments) || !state.segments.length || state.activeIdx < 0 || state.activeIdx >= state.segments.length) {
        container.innerHTML = '<div class="empty">Selecciona un subtítulo en la línea de tiempo</div>';
        return;
    }
    
    const seg = state.segments[state.activeIdx];
    const t = videoPlayer ? videoPlayer.currentTime : 0;
    const isInRange = t > seg.start + 0.1 && t < seg.end - 0.1;
    
    container.innerHTML = `
        <div class="edit-label">Texto (#${state.activeIdx + 1} de ${state.segments.length})</div>
        <textarea class="edit-textarea" id="editText">${escHtml(seg.text)}</textarea>
        <button class="ep-btn" id="btnSplitText" style="width:100%;margin-top:0.5rem;" ${seg.text.length < 2 ? 'disabled' : ''}>✂ Cortar en el texto seleccionado</button>
        <div class="edit-label" style="margin-top:0.8rem;">Tiempos (inicio - fin)</div>
        <div class="edit-time-row">
            <input class="edit-time-input" id="editStart" value="${fmtTime(seg.start)}">
            <input class="edit-time-input" id="editEnd" value="${fmtTime(seg.end)}">
        </div>
        <div style="font-size:0.85rem;color:var(--text3);margin-top:0.5rem;text-align:center;">
            ${isInRange 
                ? `<span style="color:var(--success);">✓ Playhead posicionado (${fmtTime(t)}) - Presioná S para cortar</span>` 
                : `<span>Mové la línea roja DENTRO del subtítulo (${fmtTime(seg.start)} - ${fmtTime(seg.end)})</span>`}
        </div>
    `;
    
    $('editText').oninput = () => {
        seg.text = $('editText').value;
        updateOverlay();
        renderTimeline();
        saveSegmentsDebounced();
        // Update split button
        const splitBtn = $('btnSplitText');
        if (splitBtn) splitBtn.disabled = seg.text.length < 2;
    };
    $('editText').onblur = () => pushUndo();
    
    // Botón de cortar en texto seleccionado
    $('btnSplitText').onclick = () => splitAtSelection();
    
    };
}

// ===== SPLIT AT TEXT SELECTION =====
function splitAtSelection() {
    if (state.activeIdx < 0 || state.activeIdx >= state.segments.length) return;
    
    const textarea = $('editText');
    if (!textarea) return;
    
    const seg = state.segments[state.activeIdx];
    const text = seg.text;
    const selStart = textarea.selectionStart;
    const selEnd = textarea.selectionEnd;
    
    if (selStart === selEnd) {
        // No hay selección, preguntar por palabra
        alert('Seleccioná una parte del texto O posicioná la línea roja y presioná S');
        return;
    }
    
    // Cortar en la selección de texto
    pushUndo();
    
    const firstText = text.substring(0, selStart).trim();
    const secondText = text.substring(selEnd).trim();
    
    // Calcular posición temporal proporcional
    const totalDuration = seg.end - seg.start;
    const proportion = selStart / text.length;
    const splitTime = seg.start + totalDuration * proportion;
    
    state.segments.splice(state.activeIdx, 1,
        { start: seg.start, end: splitTime, text: firstText },
        { start: splitTime, end: seg.end, text: secondText }
    );
    
    renderTimeline();
    renderEditPanel();
}
            renderTimeline();
            saveSegmentsDebounced();
        }
    };
}

export function updateOverlay() {
    const overlay = $('subOverlay');
    const text = $('subOverlayText');
    const t = videoPlayer.currentTime;
    
    let current = null;
    for (let i = 0; i < state.segments.length; i++) {
        if (t >= state.segments[i].start && t <= state.segments[i].end) {
            current = state.segments[i];
            break;
        }
    }
    
    if (current && current.text.trim()) {
        text.textContent = current.text;
        overlay.style.display = 'block';
    } else {
        overlay.style.display = 'none';
    }
}

// ===== KEYBOARD =====
function handleKeydown(e) {
    const inInput = document.activeElement && (document.activeElement.tagName === 'TEXTAREA' || document.activeElement.tagName === 'INPUT');
    
    if (e.key === 'Escape' && cutState.isCutMode) {
        e.preventDefault();
        exitCutMode();
        $('btnSplit').style.background = '';
        $('btnSplit').style.color = '';
        $('btnSplit').innerHTML = '✂';
        setEditorEnabledFn(true);
        return;
    }
    
    if (e.ctrlKey && e.key === 'z' && !e.shiftKey) { e.preventDefault(); if (undo()) { renderTimeline(); renderEditPanel(); } return; }
    if (e.ctrlKey && (e.key === 'Z' || (e.key === 'z' && e.shiftKey))) { e.preventDefault(); if (redo()) { renderTimeline(); renderEditPanel(); } return; }
    if (e.ctrlKey && e.key === 'y') { e.preventDefault(); if (redo()) { renderTimeline(); renderEditPanel(); } return; }
    
    if (inInput) return;
    
    if (e.key === ' ') { e.preventDefault(); videoPlayer.paused ? videoPlayer.play() : videoPlayer.pause(); }
    if (e.key === 'Delete') { e.preventDefault(); deleteActive(); }
    if (e.key === 's' || e.key === 'S') { e.preventDefault(); handleSplit(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); if (state.segments.length) { state.activeIdx = state.activeIdx <= 0 ? state.segments.length - 1 : state.activeIdx - 1; highlightBlocks(); renderEditPanel(); } }
    if (e.key === 'ArrowDown') { e.preventDefault(); if (state.segments.length) { state.activeIdx = state.activeIdx >= state.segments.length - 1 ? 0 : state.activeIdx + 1; highlightBlocks(); renderEditPanel(); } }
}

// ===== UTILS =====
function setProgress(visible, text) {
    const prog = $('progress');
    if (!prog) return;
    prog.classList.toggle('active', !!visible);
    if (text != null) $('progressText').textContent = text;
    if (!visible) $('progressBar').style.width = '0%';
}

function fmtTime(sec) {
    if (sec == null || isNaN(sec)) return '0:00.0';
    const m = Math.floor(sec / 60);
    return m + ':' + (sec % 60).toFixed(1).padStart(4, '0');
}

function parseTime(str) {
    try {
        if (str.includes(':')) {
            const [m, s] = str.split(':');
            return parseInt(m) * 60 + parseFloat(s);
        }
        return parseFloat(str);
    } catch { return null; }
}

function escHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
