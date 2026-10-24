() => {
  if (window.pdf2zhPreviewTimer) clearInterval(window.pdf2zhPreviewTimer);
  let raw = '', data = {pages: {}}, overlay = null, activeCanvas = null;
  window.pdf2zhPreviewTimer = setInterval(() => {
    const payload = document.querySelector('#live-page-updates [data-preview]');
    const next = payload?.getAttribute('data-preview') || '';
    if (next !== raw) {
      raw = next;
      try { data = raw ? JSON.parse(raw) : {pages: {}}; }
      catch { data = {pages: {}}; }
    }
    const host = document.querySelector('#document-preview');
    const canvas = host?.querySelector('canvas');
    const input = host?.querySelector('input[type="number"]');
    if (!canvas || !input) return;
    if (activeCanvas !== canvas) {
      overlay?.remove();
      overlay = document.createElement('img');
      overlay.alt = '当前页译文';
      overlay.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;object-fit:contain;pointer-events:none;display:none;background:white;';
      canvas.parentElement.style.position = 'relative';
      canvas.parentElement.appendChild(overlay);
      activeCanvas = canvas;
    }
    const page = Number(input.value) || 1;
    const url = data.pages?.[String(page)];
    if (url) {
      if (overlay.getAttribute('src') !== url) overlay.src = url;
      overlay.style.display = 'block';
      canvas.style.visibility = 'hidden';
    } else {
      overlay.style.display = 'none';
      canvas.style.visibility = '';
    }
  }, 150);
}
