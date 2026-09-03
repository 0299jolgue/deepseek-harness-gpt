// Download-card renderer for Jolgue AI. Loaded by the chat page when present.
window.JolgueDownload = {
  render(container, tools) {
    const downloads = (tools || []).filter(t => t && t.download_url);
    for (const item of downloads) {
      const a = document.createElement('a');
      a.className = 'download-card';
      a.href = item.download_url;
      a.setAttribute('download', '');
      a.textContent = `⬇️ Download ${item.path || 'file'}`;
      container.appendChild(a);
    }
  }
};
