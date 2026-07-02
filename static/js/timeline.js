/**
 * Timeline Module for Subtitulador
 * Handles timeline rendering, block manipulation, and cut mode
 */

import { state, cutState, dragState, history, pushUndo } from './state.js';
import { saveSegments } from './api.js';

// ===== EXPORT REFERENCES (will be set by init) =====
let $, videoPlayer, track, playhead, timeline, renderEditPanel, updateOverlay, setEditorEnabled;

/**
 * Initialize timeline module with dependencies
 */
export function initTimeline(dependencies) {
    $ = dependencies.$;
    videoPlayer = dependencies.videoPlayer;
    track = dependencies.track;
    playhead = dependencies.playhead;
    timeline = dependencies.timeline;
    renderEditPanel = dependencies.renderEditPanel;
    updateOverlay = dependencies.updateOverlay;
    setEditorEnabled = dependencies.setEditorEnabled;
}

// ===== TIMELINE RENDER =====
export function renderTimeline() {
    if (!state.videoDuration || !Array.isArray(state.segments) || !state.segments.length) return;
    
    const totalW = state.videoDuration * state.pxPerSec;
    track.style.width = totalW + 'px';
    document.getElementById('timelineRuler').style.width = totalW + 'px';
    
    renderRuler();
    renderBlocks();
    renderCutLine();
}

function renderRuler() {
    const ruler = document.getElementById('timelineRuler');
    ruler.innerHTML = '';
    
    const step = state.pxPerSec >= 60 ? 1 : state.pxPerSec >= 30 ? 2 : 5;
    
    for (let t = 0; t <= state.videoDuration; t += step) {
        const tick = document.createElement('div');
        tick.className = 'tick';
        tick.style.left = (t * state.pxPerSec) + 'px';
        tick.textContent = fmtShort(t);
        ruler.appendChild(tick);
    }
}

function renderBlocks() {
    track.innerHTML = '';
    
    state.segments.forEach((seg, i) => {
        const block = document.createElement('div');
        block.className = 'timeline-block' + (i === state.activeIdx ? ' active' : '');
        block.style.left = (seg.start * state.pxPerSec) + 'px';
        block.style.width = Math.max((seg.end - seg.start) * state.pxPerSec, 22) + 'px';
        block.textContent = seg.text;
        block.dataset.idx = i;
        
        // Resize handles
        const rl = document.createElement('div');
        rl.className = 'resize-l';
        const rr = document.createElement('div');
        rr.className = 'resize-r';
        block.appendChild(rl);
        block.appendChild(rr);
        
        // Click to select
        block.onclick = e => {
            if (e.target.closest('.resize-l') || e.target.closest('.resize-r')) return;
            state.activeIdx = i;
            highlightBlocks();
            renderEditPanel();
            updateOverlay();
        };
        
        // Double-click for inline edit
        block.ondblclick = e => {
            if (e.target.closest('.resize-l') || e.target.closest('.resize-r')) return;
            startInlineEdit(block, i);
        };
        
        // Drag move
        block.onmousedown = e => {
            if (e.target.closest('.resize-l') || e.target.closest('.resize-r')) return;
            if (e.button === 0) {
                startMoveDrag(e, i);
            }
        };
        
        // Resize handlers
        rl.onmousedown = e => { e.stopPropagation(); startResize(e, i, 'left'); };
        rr.onmousedown = e => { e.stopPropagation(); startResize(e, i, 'right'); };
        
        track.appendChild(block);
    });
    
    highlightBlocks();
}

export function highlightBlocks() {
    track.querySelectorAll('.timeline-block').forEach((b, i) => {
        b.classList.toggle('active', i === state.activeIdx);
    });
}

// ===== CUT LINE =====
function renderCutLine() {
    const cutLine = document.getElementById('cutLine');
    if (!cutLine) return;
    
    if (!cutState.isCutMode) {
        cutLine.style.display = 'none';
        return;
    }
    
    const seg = state.segments[cutState.cutSegmentIdx];
    if (!seg) return;
    
    cutLine.style.display = 'block';
    cutLine.style.left = (cutState.cutPosition * state.pxPerSec) + 'px';
    
    cutLine.onmousedown = startCutDrag;
}

function startCutDrag(e) {
    e.preventDefault();
    e.stopPropagation();
    
    const seg = state.segments[cutState.cutSegmentIdx];
    if (!seg) return;
    
    const rect = timeline.getBoundingClientRect();
    const scrollLeft = timeline.scrollLeft;
    
    function getTimeFromMouse(clientX) {
        const x = clientX - rect.left + scrollLeft;
        return x / state.pxPerSec;
    }
    
    function onMove(e2) {
        const newTime = getTimeFromMouse(e2.clientX);
        cutState.cutPosition = Math.max(seg.start + 0.05, Math.min(seg.end - 0.05, newTime));
        const cutLine = document.getElementById('cutLine');
        if (cutLine) {
            cutLine.style.left = (cutState.cutPosition * state.pxPerSec) + 'px';
        }
    }
    
    function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
    }
    
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
}

// ===== MOVE DRAG =====
function startMoveDrag(e, idx) {
    if (e.button !== 0) return;
    if (!Array.isArray(state.segments) || !state.segments.length) return;
    
    pushUndo();
    
    dragState.isDraggingMove = true;
    dragState.dragIdx = idx;
    dragState.dragOrigStart = state.segments[idx].start;
    dragState.dragOrigEnd = state.segments[idx].end;
    dragState.dragStartX = e.clientX;
    
    e.preventDefault();
    document.body.style.cursor = 'grabbing';
    
    function onMove(e2) {
        if (!dragState.isDraggingMove) return;
        const seg = state.segments[dragState.dragIdx];
        if (!seg) return;
        
        const dxSecs = (e2.clientX - dragState.dragStartX) / state.pxPerSec;
        const dur = dragState.dragOrigEnd - dragState.dragOrigStart;
        
        const newStart = dragState.dragOrigStart + dxSecs;
        
        const minDur = 0.2;
        let clampedStart = Math.max(0, Math.min(newStart, Math.max(0, state.videoDuration - dur)));
        let clampedEnd = clampedStart + dur;
        
        if ((clampedEnd - clampedStart) < minDur) {
            clampedEnd = clampedStart + minDur;
            if (clampedEnd > state.videoDuration) {
                clampedEnd = state.videoDuration;
                clampedStart = Math.max(0, clampedEnd - minDur);
            }
        }
        
        seg.start = clampedStart;
        seg.end = clampedEnd;
        
        state.activeIdx = dragState.dragIdx;
        renderTimeline();
        renderEditPanel();
        updateOverlay();
    }
    
    function onUp() {
        dragState.isDraggingMove = false;
        dragState.dragIdx = -1;
        document.body.style.cursor = '';
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        saveSegmentsDebounced();
    }
    
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
}

// ===== RESIZE DRAG =====
function startResize(e, idx, side) {
    pushUndo();
    const seg = state.segments[idx];
    const startX = e.clientX;
    const origStart = seg.start;
    const origEnd = seg.end;
    
    function onMove(e2) {
        const dx = (e2.clientX - startX) / state.pxPerSec;
        if (side === 'left') {
            seg.start = Math.max(0, Math.min(origStart + dx, seg.end - 0.2));
        } else {
            seg.end = Math.min(state.videoDuration, Math.max(origEnd + dx, seg.start + 0.2));
        }
        renderTimeline();
        renderEditPanel();
    }
    
    function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        saveSegmentsDebounced();
    }
    
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
}

// ===== INLINE EDIT =====
function startInlineEdit(block, idx) {
    const seg = state.segments[idx];
    if (!seg) return;
    
    const originalText = seg.text;
    
    const textarea = document.createElement('textarea');
    textarea.className = 'edit-textarea';
    textarea.value = seg.text;
    textarea.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;box-sizing:border-box;resize:none;border:none;background:var(--bg);color:var(--text);font-family:inherit;font-size:1rem;padding:4px 8px;';
    
    block.innerHTML = '';
    block.appendChild(textarea);
    textarea.focus();
    textarea.select();
    
    function save() {
        seg.text = textarea.value;
        renderTimeline();
        renderEditPanel();
        updateOverlay();
        saveSegmentsDebounced();
    }
    
    textarea.onblur = save;
    textarea.onkeydown = e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            save();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            seg.text = originalText;
            renderTimeline();
            renderEditPanel();
            updateOverlay();
            textarea.blur();
        }
    };
}

// ===== PLAYHEAD SYNC =====
export function updatePlayhead() {
    const t = videoPlayer.currentTime;
    $('timeDisplay').textContent = fmtTime(t);
    playhead.style.left = (t * state.pxPerSec) + 'px';
    
    // Auto-select current segment
    if (!dragState.isDraggingMove) {
        for (let i = 0; i < state.segments.length; i++) {
            if (t >= state.segments[i].start && t <= state.segments[i].end) {
                if (i !== state.activeIdx) {
                    state.activeIdx = i;
                    highlightBlocks();
                    renderEditPanel();
                }
                break;
            }
        }
    }
    
    // Auto-scroll timeline
    const scrollLeft = timeline.scrollLeft;
    const viewW = timeline.clientWidth;
    const headPos = t * state.pxPerSec;
    if (headPos < scrollLeft || headPos > scrollLeft + viewW - 50) {
        timeline.scrollLeft = headPos - viewW / 3;
    }
}

// ===== UTILS =====
function fmtShort(sec) {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return m + ':' + String(s).padStart(2, '0');
}

function fmtTime(sec) {
    if (sec == null || isNaN(sec)) return '0:00.0';
    const m = Math.floor(sec / 60);
    return m + ':' + (sec % 60).toFixed(1).padStart(4, '0');
}

// ===== DEBOUNCED SAVE =====
let saveTO;
function saveSegmentsDebounced() {
    clearTimeout(saveTO);
    saveTO = setTimeout(() => {
        if (state.sessionId && Array.isArray(state.segments) && state.segments.length) {
            saveSegments(state.sessionId, state.segments).catch(() => {});
        }
    }, 500);
}

export { saveSegmentsDebounced };
