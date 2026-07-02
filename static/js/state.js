/**
 * State Management Module for Subtitulador
 * Centralizes all application state
 */

// ===== MAIN STATE =====
export const state = {
    selectedFile: null,
    sessionId: null,
    segments: [],
    activeIdx: -1,
    videoDuration: 0,
    pxPerSec: 80  // zoom level as px/sec
};

// ===== UI STATE =====
export const uiState = {
    theme: localStorage.getItem('theme') || 'dark',
    videoCompact: localStorage.getItem('videoCompact') === '1'
};

// ===== UNDO/REDO STATE =====
export const history = {
    undo: [],
    redo: [],
    maxUndo: 50
};

// ===== DRAG STATE =====
export const dragState = {
    isDraggingMove: false,
    dragIdx: -1,
    dragOrigStart: 0,
    dragOrigEnd: 0,
    dragStartX: 0
};

// ===== RESIZE STATE =====
export const resizeState = {
    isResizing: false,
    isResizingVertical: false,
    startX: 0,
    startY: 0,
    startWidth: 0,
    startHeight: 0
};

// ===== CUT MODE STATE =====
export const cutState = {
    isCutMode: false,
    cutSegmentIdx: -1,
    cutPosition: 0
};

// ===== STATE METHODS =====
export function resetState() {
    state.selectedFile = null;
    state.sessionId = null;
    state.segments = [];
    state.activeIdx = -1;
    state.videoDuration = 0;
    
    history.undo = [];
    history.redo = [];
    
    exitCutMode();
}

export function enterCutMode() {
    if (state.activeIdx < 0 || state.activeIdx >= state.segments.length) return false;
    
    cutState.isCutMode = true;
    cutState.cutSegmentIdx = state.activeIdx;
    
    const seg = state.segments[state.activeIdx];
    cutState.cutPosition = (seg.start + seg.end) / 2;
    
    return true;
}

export function exitCutMode() {
    cutState.isCutMode = false;
    cutState.cutSegmentIdx = -1;
    cutState.cutPosition = 0;
}

export function pushUndo() {
    history.undo.push(JSON.stringify(state.segments));
    if (history.undo.length > history.maxUndo) {
        history.undo.shift();
    }
    history.redo = [];
}

export function undo() {
    if (!history.undo.length) return false;
    history.redo.push(JSON.stringify(state.segments));
    state.segments = JSON.parse(history.undo.pop());
    state.activeIdx = Math.min(state.activeIdx, state.segments.length - 1);
    return true;
}

export function redo() {
    if (!history.redo.length) return false;
    history.undo.push(JSON.stringify(state.segments));
    state.segments = JSON.parse(history.redo.pop());
    return true;
}

// ===== THEME METHODS =====
export function toggleTheme() {
    uiState.theme = uiState.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = uiState.theme;
    localStorage.setItem('theme', uiState.theme);
    return uiState.theme;
}

export function toggleVideoCompact() {
    uiState.videoCompact = !uiState.videoCompact;
    document.body.classList.toggle('video-compact', uiState.videoCompact);
    localStorage.setItem('videoCompact', uiState.videoCompact ? '1' : '0');
    return uiState.videoCompact;
}

export function initTheme() {
    document.documentElement.dataset.theme = uiState.theme;
    if (uiState.videoCompact) {
        document.body.classList.add('video-compact');
    }
}
