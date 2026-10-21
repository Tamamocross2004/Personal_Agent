const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("desktop", {
  connection: () => ipcRenderer.invoke("connection"),
  pickFolder: () => ipcRenderer.invoke("pick-folder"),
  openTask: (path) => ipcRenderer.invoke("open-task", path),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
});
