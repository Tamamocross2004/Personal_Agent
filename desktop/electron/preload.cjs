const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("desktop", {
  connection: () => ipcRenderer.invoke("connection"),
  openTask: (path) => ipcRenderer.invoke("open-task", path),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
});
