const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("ual", {
  getState: () => ipcRenderer.invoke("backend:state"),
  getAssets: () => ipcRenderer.invoke("backend:assets"),
  favorites: () => ipcRenderer.invoke("backend:favorites"),
  setFavorite: (assetKey, favorite) => ipcRenderer.invoke("backend:set-favorite", assetKey, favorite),
  resync: () => ipcRenderer.invoke("backend:resync"),
  resyncApply: (planHash) => ipcRenderer.invoke("backend:resync-apply", planHash),
  cleanupPlan: () => ipcRenderer.invoke("backend:cleanup-plan"),
  cleanupApply: (planHash) => ipcRenderer.invoke("backend:cleanup-apply", planHash),
  organizePlan: () => ipcRenderer.invoke("backend:organize-plan"),
  organizeApply: (planHash) => ipcRenderer.invoke("backend:organize-apply", planHash),
  enrichStart: () => ipcRenderer.invoke("backend:enrich-start"),
  enrichCancel: () => ipcRenderer.invoke("backend:enrich-cancel"),
  getJob: (jobId) => ipcRenderer.invoke("backend:job", jobId),
  getAction: (actionId) => ipcRenderer.invoke("backend:action", actionId),
  getStorage: () => ipcRenderer.invoke("storage:get"),
  chooseStorageFolder: (currentPath) => ipcRenderer.invoke("storage:choose-folder", currentPath),
  saveStorage: (selectedPath) => ipcRenderer.invoke("storage:save", selectedPath),
  openExternal: (url) => ipcRenderer.invoke("shell:open-external", url),
  revealItem: (filePath) => ipcRenderer.invoke("shell:reveal-item", filePath),
});
