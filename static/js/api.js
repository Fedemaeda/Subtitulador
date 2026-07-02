/**
 * API Module for Subtitulador
 * Handles all communication with the backend
 */

const API_BASE = '';

/**
 * Fetch GPU info from server
 * @returns {Promise<{gpu: boolean, device: string, compute_type: string}>}
 */
export async function fetchGPUInfo() {
    const response = await fetch(`${API_BASE}/gpu`);
    return response.json();
}

/**
 * Upload and transcribe video
 * @param {FormData} formData - Form data with video file and options
 * @returns {Promise<{session_id: string, segments: Array, language: string, ext: string, filename: string}>}
 */
export async function transcribeVideo(formData) {
    const response = await fetch(`${API_BASE}/transcribe`, {
        method: 'POST',
        body: formData
    });
    
    if (!response.ok) {
        const error = await response.json().catch(() => ({ error: 'Error al transcribir' }));
        throw new Error(error.error || response.statusText);
    }
    
    return response.json();
}

/**
 * Get segments for a session
 * @param {string} sessionId 
 * @returns {Promise<Array>}
 */
export async function getSegments(sessionId) {
    const response = await fetch(`${API_BASE}/api/session/${sessionId}/segments`);
    if (!response.ok) throw new Error('Session not found');
    return response.json();
}

/**
 * Save segments to session
 * @param {string} sessionId 
 * @param {Array} segments 
 * @returns {Promise<void>}
 */
export async function saveSegments(sessionId, segments) {
    const response = await fetch(`${API_BASE}/api/session/${sessionId}/segments`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ segments })
    });
    if (!response.ok) throw new Error('Failed to save segments');
}

/**
 * Get video URL for session
 * @param {string} sessionId 
 * @returns {string} URL to video
 */
export function getVideoURL(sessionId) {
    return `${API_BASE}/api/session/${sessionId}/video`;
}

/**
 * Get SRT download URL
 * @param {string} sessionId 
 * @returns {string} URL to download SRT
 */
export function getSRTURL(sessionId) {
    return `${API_BASE}/api/session/${sessionId}/srt`;
}

/**
 * Burn subtitles into video
 * @param {string} sessionId 
 * @param {Object} options - { font_size, margin_v }
 * @returns {Promise<Blob>} Video blob with burned subtitles
 */
export async function burnSubtitles(sessionId, options = {}) {
    const response = await fetch(`${API_BASE}/api/session/${sessionId}/burn`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(options)
    });
    
    if (!response.ok) {
        const error = await response.json().catch(() => ({ error: 'Error al quemar subtítulos' }));
        throw new Error(error.error);
    }
    
    return response.blob();
}

/**
 * Get list of available models
 * @returns {Promise<string[]>}
 */
export async function getModels() {
    const response = await fetch(`${API_BASE}/models`);
    return response.json();
}
