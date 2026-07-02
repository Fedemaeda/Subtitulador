/**
 * Main Entry Point for Subtitulador
 * Initializes the application
 */

import { initEditor } from './editor.js';
import { fetchGPUInfo } from './api.js';

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', async () => {
    console.log('Subtitulador v2.0 - Modular Edition');
    
    // Initialize editor
    await initEditor();
    
    // Fetch GPU info
    try {
        const gpuInfo = await fetchGPUInfo();
        document.getElementById('gpuBadge').textContent = gpuInfo.gpu ? 'GPU' : 'CPU';
    } catch (e) {
        console.warn('Could not fetch GPU info:', e);
    }
    
    // Version badge
    const vEl = document.getElementById('uiVersion');
    if (vEl) vEl.textContent = 'v2.0';
});

// Divider resizing (horizontal and vertical)
function initDividerResizing() {
    const divider = document.getElementById('divider');
    const hDivider = document.getElementById('hDivider');
    const editorTop = document.querySelector('.editor-top');
    const editPanel = document.getElementById('editPanel');
    
    let isResizing = false;
    let isResizingVertical = false;
    let startX = 0;
    let startY = 0;
    let startWidth = 0;
    let startHeight = 0;
    
    // Vertical divider (edit panel width)
    if (divider && editPanel) {
        divider.addEventListener('mousedown', (e) => {
            e.preventDefault();
            isResizing = true;
            startX = e.clientX;
            startWidth = editPanel.offsetWidth;
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
        });
    }
    
    // Horizontal divider (video height)
    if (hDivider && editorTop) {
        hDivider.addEventListener('mousedown', (e) => {
            e.preventDefault();
            isResizingVertical = true;
            startY = e.clientY;
            startHeight = editorTop.offsetHeight;
            document.body.style.cursor = 'row-resize';
            document.body.style.userSelect = 'none';
        });
    }
    
    document.addEventListener('mousemove', (e) => {
        if (isResizing && editPanel) {
            const dx = e.clientX - startX;
            const newWidth = Math.max(240, Math.min(450, startWidth - dx));
            editPanel.style.width = newWidth + 'px';
        }
        if (isResizingVertical && editorTop) {
            const dy = e.clientY - startY;
            const newHeight = Math.max(150, Math.min(window.innerHeight * 0.6, startHeight + dy));
            editorTop.style.height = newHeight + 'px';
        }
    });
    
    document.addEventListener('mouseup', () => {
        if (isResizing) {
            isResizing = false;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }
        if (isResizingVertical) {
            isResizingVertical = false;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }
    });
}

// Initialize dividers after DOM ready
document.addEventListener('DOMContentLoaded', initDividerResizing);
