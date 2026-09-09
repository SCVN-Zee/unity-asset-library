// Editor-only companion for bin/import_packages.py. Never replays an unconfirmed import.
using System;
using System.Diagnostics;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace UnityAssetLibrary
{
    [InitializeOnLoad]
    internal static class UnityAssetLibraryImport
    {
        internal const string BridgeVersion = "1";
        private static readonly int EditorPid = Process.GetCurrentProcess().Id;
        private static readonly string DirectoryPath = Path.Combine(Path.GetDirectoryName(Application.dataPath), "Library/UALImport");
        private static Request active;
        private static double nextPoll;
        private static double nextHeartbeat;
        private static double settleUntil;
        private static double recoveryDeadline;

        static UnityAssetLibraryImport()
        {
            AssetDatabase.importPackageCompleted += name => FinishMatching(name, "imported", null);
            AssetDatabase.importPackageFailed += (name, error) => FinishMatching(name, "failed", error);
            AssetDatabase.importPackageCancelled += name => FinishMatching(name, "failed", "Unity cancelled the package import.");
            AssemblyReloadEvents.beforeAssemblyReload += () => WriteHeartbeat("busy");
            EditorApplication.quitting += () => WriteHeartbeat("busy");
            active = Read<Request>("active.json");
            // A reload can interrupt managed callbacks. Keep the durable claim and
            // accept a late completion event, but never call ImportPackage again.
            if (active != null) recoveryDeadline = EditorApplication.timeSinceStartup + 15;
            settleUntil = EditorApplication.timeSinceStartup + 1;
            EditorApplication.update += Tick;
        }

        private static void Tick()
        {
            double now = EditorApplication.timeSinceStartup;
            if (now < nextPoll) return;
            nextPoll = now + 0.5;
            try
            {
                bool busy = EditorApplication.isCompiling || EditorApplication.isUpdating ||
                    EditorApplication.isPlayingOrWillChangePlaymode || now < settleUntil;
                if (now >= nextHeartbeat)
                {
                    WriteHeartbeat(busy || active != null ? "busy" : "ready");
                    nextHeartbeat = now + 2;
                }
                if (busy) return;
                if (active != null)
                {
                    Done previous = Read<Done>("done.json");
                    if (previous != null && previous.id == active.id)
                    {
                        ClearClaim(active.id);
                        active = null;
                    }
                    else if (recoveryDeadline > 0 && now >= recoveryDeadline)
                    {
                        Finish("failed", "Editor reloaded before completion could be confirmed. Inspect the project before retrying; the package was not replayed.");
                    }
                    return;
                }
                Request request = Read<Request>("request.json");
                if (request == null) return;
                if (string.IsNullOrEmpty(request.id) || string.IsNullOrEmpty(request.package))
                    throw new InvalidDataException("Import request requires an id and a package path.");
                Done done = Read<Done>("done.json");
                if (done != null && done.id == request.id)
                {
                    DeleteMatchingRequest(request.id);
                    return;
                }
                active = request;
                WriteAtomic("active.json", JsonUtility.ToJson(active));
                WriteHeartbeat("busy");
                if (active.editor_pid != EditorPid)
                {
                    Finish("failed", "The request belongs to an earlier Editor session and was not executed.");
                    return;
                }
                if (!Path.IsPathRooted(active.package) || !active.package.EndsWith(".unitypackage", StringComparison.OrdinalIgnoreCase) || !File.Exists(active.package))
                {
                    Finish("failed", "The requested .unitypackage file is unavailable.");
                    return;
                }
                try { AssetDatabase.ImportPackage(active.package, false); }
                catch (Exception error) { Finish("failed", error.Message); }
                // ImportPackage returning is NOT the completion signal. Its
                // callbacks own the result, including imports which reload scripts.
            }
            catch (Exception error)
            {
                UnityEngine.Debug.LogError("Unity Asset Library import bridge: " + error.Message);
                nextPoll = now + 5;
            }
        }

        private static void FinishMatching(string name, string status, string error)
        {
            if (active == null || !string.Equals(Path.GetFileNameWithoutExtension(name),
                Path.GetFileNameWithoutExtension(active.package), StringComparison.OrdinalIgnoreCase)) return;
            Finish(status, error);
        }

        private static void Finish(string status, string error)
        {
            if (active == null) return;
            string id = active.id;
            // Commit the receipt before removing the claim. A crash between these
            // writes is recoverable by exact id, without repeating the import.
            WriteAtomic("done.json", JsonUtility.ToJson(new Done { id = id, status = status, error = error }));
            ClearClaim(id);
            active = null;
            recoveryDeadline = 0;
            settleUntil = EditorApplication.timeSinceStartup + 1;
            WriteHeartbeat("busy");
        }

        private static void ClearClaim(string id)
        {
            DeleteMatchingRequest(id);
            File.Delete(Path.Combine(DirectoryPath, "active.json"));
        }

        private static void DeleteMatchingRequest(string id)
        {
            Request request = Read<Request>("request.json");
            if (request != null && request.id == id) File.Delete(Path.Combine(DirectoryPath, "request.json"));
        }

        private static T Read<T>(string name) where T : class
        {
            string path = Path.Combine(DirectoryPath, name);
            if (!File.Exists(path)) return null;
            return JsonUtility.FromJson<T>(File.ReadAllText(path));
        }

        private static void WriteHeartbeat(string status)
        {
            WriteAtomic("heartbeat.json", JsonUtility.ToJson(new Heartbeat {
                pid = EditorPid, bridge_version = BridgeVersion,
                status = status, request_id = active == null ? "" : active.id,
                updated = DateTime.UtcNow.ToString("o")
            }));
        }

        private static void WriteAtomic(string name, string text)
        {
            Directory.CreateDirectory(DirectoryPath);
            string path = Path.Combine(DirectoryPath, name);
            string temporary = path + ".tmp";
            File.WriteAllText(temporary, text);
            if (File.Exists(path)) File.Replace(temporary, path, null);
            else File.Move(temporary, path);
        }

        [Serializable] private class Request { public string id; public string package; public int editor_pid; }
        [Serializable] private class Done { public string id; public string status; public string error; }
        [Serializable] private class Heartbeat {
            public int pid;
            public string bridge_version;
            public string status;
            public string request_id;
            public string updated;
        }
    }
}
