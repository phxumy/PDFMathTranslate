() => {
  window.pdf2zhDropController?.abort();
  const controller = new AbortController();
  window.pdf2zhDropController = controller;
  const options = {capture: true, signal: controller.signal};
  const zone = () => document.getElementById('pdf-upload');
  const hasFiles = event => Array.from(event.dataTransfer?.types || []).includes('Files');
  const clear = () => zone()?.classList.remove('pdf2zh-dragover');

  document.addEventListener('dragover', event => {
    if (!hasFiles(event)) return;
    // A dropped PDF must never replace the application's page in the browser.
    event.preventDefault();
    const host = zone();
    if (host?.contains(event.target)) host.classList.add('pdf2zh-dragover');
    else clear();
  }, options);
  document.addEventListener('dragleave', event => {
    if (!zone()?.contains(event.relatedTarget)) clear();
  }, options);
  document.addEventListener('dragend', clear, options);
  document.addEventListener('drop', event => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    clear();
    const host = zone();
    if (!host?.contains(event.target)) return;
    // Gradio keeps its native append uploader in a small icon once a list is
    // present. Forward drops on the list to that uploader, retaining its file
    // validation, upload transport, append order and change notification.
    const uploader = host.querySelector('button[aria-dropeffect="copy"]');
    if (!uploader || uploader.disabled || uploader.querySelector('input')?.disabled) return;
    if (uploader.contains(event.target)) return;
    event.stopPropagation();
    uploader.dispatchEvent(new DragEvent('drop', {
      bubbles: true, cancelable: true, dataTransfer: event.dataTransfer
    }));
  }, options);
}
