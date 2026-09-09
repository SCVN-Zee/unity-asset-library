const { contextBridge, ipcRenderer } = require("electron");

window.addEventListener("DOMContentLoaded", () => {
  if (process.platform === "darwin") document.documentElement.classList.add("native-titlebar");
}, { once: true });

contextBridge.exposeInMainWorld("ual", {
  importProjects: () => ipcRenderer.invoke("import:projects"),
  chooseImportProject: () => ipcRenderer.invoke("import:choose-project"),
  inspectImportProject: (project) => ipcRenderer.invoke("import:inspect", project),
  installImportBridge: (project) => ipcRenderer.invoke("import:install-bridge", project),
  startImport: (request) => ipcRenderer.invoke("import:start", request),
  getImport: () => ipcRenderer.invoke("import:status"),
  stopImport: (id) => ipcRenderer.invoke("import:stop", id),
  getState: () => ipcRenderer.invoke("backend:state"),
  getAssets: () => ipcRenderer.invoke("backend:assets"),
  getTags: () => ipcRenderer.invoke("backend:tags"),
  mutateTags: (change) => ipcRenderer.invoke("backend:mutate-tags", change),
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
  getPreferences: () => ipcRenderer.invoke("prefs:get"),
  setPreferences: (prefs, libraryRoot, onlyIfMissing) => ipcRenderer.invoke("prefs:set", prefs, libraryRoot, onlyIfMissing),
  getLegacyLibraryRoot: () => ipcRenderer.invoke("prefs:legacy-root"),
  getStorage: () => ipcRenderer.invoke("storage:get"),
  answerPortChoice: (id, choice) => ipcRenderer.invoke("storage:port-choice", id, choice),
  retryBackend: () => ipcRenderer.invoke("storage:retry"),
  chooseStorageFolder: (currentPath) => ipcRenderer.invoke("storage:choose-folder", currentPath),
  saveStorage: (selectedPath) => ipcRenderer.invoke("storage:save", selectedPath),
  openExternal: (url) => ipcRenderer.invoke("shell:open-external", url),
  revealItem: (filePath) => ipcRenderer.invoke("shell:reveal-item", filePath),
});
