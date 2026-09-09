// Closed-batch import runner companion for bin/import_packages.py. This file is
// deployed temporarily into a closed project. At a safe batch boundary, Unity
// removes it through AssetDatabase before exiting; the host provides fallback
// cleanup. Live-editor imports use UnityAssetLibraryImport.cs instead;
// it is inert unless this process was explicitly launched with its entry method.
using System;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace UnityAssetLibraryBatch
{
    [InitializeOnLoad]
    public static class BatchRunner
    {
        private const double OrphanClaimGraceSeconds = 30.0;
        private static readonly string DirectoryPath = Path.Combine(
            Path.GetDirectoryName(Application.dataPath), "Library/UALImport");
        private static BatchRequest batch;
        private static double nextPoll;
        private static double settleUntil;
        private static double waitDeadline;
        private static double claimAt;
        private static string inFlightPackage;
        private static bool claimFromThisDomain;
        private static bool finishing;
        private static int exitCode;

        static BatchRunner()
        {
            string[] args = Environment.GetCommandLineArgs();
            int entry = Array.IndexOf(args, "-executeMethod");
            if (entry < 0 || entry + 1 >= args.Length ||
                args[entry + 1] != "UnityAssetLibraryBatch.BatchRunner.Run") return;
            AssetDatabase.importPackageCompleted += name => OnPackageEvent(name, "imported", null);
            AssetDatabase.importPackageFailed += (name, error) => OnPackageEvent(name, "failed", error);
            AssetDatabase.importPackageCancelled += name => OnPackageEvent(name, "failed", "Unity cancelled the package import.");
            batch = Read<BatchRequest>("batch.json");
            claimAt = EditorApplication.timeSinceStartup;
            settleUntil = claimAt + 1;
            EditorApplication.update += Tick;
        }

        // -executeMethod entry. All state is disk-driven (batch.json, receipts,
        // claim) so a domain reload mid-import resumes without replaying an
        // unconfirmed import.
        public static void Run()
        {
            if (batch == null)
            {
                Debug.LogError("Unity Asset Library batch request is missing.");
                EditorApplication.Exit(1);
            }
        }

        private static string ReceiptPath(int index)
        {
            return Path.Combine(DirectoryPath, "batch-receipts", index + ".json");
        }

        private static bool HasReceipt(int index)
        {
            return File.Exists(ReceiptPath(index));
        }

        private static void WriteReceipt(int index, string status, string error)
        {
            WriteJson(ReceiptPath(index), JsonUtility.ToJson(new BatchReceipt { index = index, status = status, error = error }));
        }

        private static void Tick()
        {
            double now = EditorApplication.timeSinceStartup;
            if (finishing) return;
            if (batch == null)
            {
                batch = Read<BatchRequest>("batch.json");
                if (batch == null) return; // Inert: this Editor was not started for a batch.
            }
            if (now < nextPoll) return;
            nextPoll = now + 0.25;
            try
            {
                bool busy = EditorApplication.isCompiling || EditorApplication.isUpdating ||
                    EditorApplication.isPlayingOrWillChangePlaymode || now < settleUntil;
                if (busy) return;

                // An unresolved claim ALWAYS wins: an import is in flight, so no
                // stop, no failure receipt, nothing may exit the Editor mid-write.
                // The package is NEVER replayed: callbacks own the result in this
                // session; only after a domain reload (the claim was not written
                // by this domain) does a grace period fail the batch instead of
                // re-importing.
                Claim claim = Read<Claim>("batch-active.json");
                if (claim != null)
                {
                    if (HasReceipt(claim.index))
                    {
                        File.Delete(Path.Combine(DirectoryPath, "batch-active.json"));
                        claimFromThisDomain = false;
                        return;
                    }
                    if (!claimFromThisDomain && now - claimAt >= OrphanClaimGraceSeconds)
                    {
                        WriteReceipt(claim.index, "failed", "Editor reloaded before completion could be confirmed. The package was not replayed; inspect the project before retrying.");
                        Finish();
                    }
                    return;
                }

                if (File.Exists(batch.stop))
                {
                    CancelRemaining();
                    return;
                }

                // Ordered stop-on-failure: walk receipts in package order and stop
                // at the FIRST gap. A failure receipt is only honored once every
                // earlier package has a receipt; a future staging failure (whose
                // receipt the host writes early) waits its turn and can never exit
                // the Editor while an earlier package is still importing.
                int next = -1;
                for (int i = 0; i < batch.packages.Length; i++)
                {
                    BatchEntry entry = batch.packages[i];
                    if (!HasReceipt(entry.index)) { next = i; break; }
                    BatchReceipt receipt = ReadReceipt(entry.index);
                    if (receipt != null && receipt.status == "failed")
                    {
                        Finish();
                        return;
                    }
                }
                if (next < 0)
                {
                    Finish();
                    return;
                }
                BatchEntry nextEntry = batch.packages[next];

                if (!IsSafePackagePath(nextEntry.package) || !File.Exists(nextEntry.package))
                {
                    // Staging may legitimately still be running: the host touches a
                    // heartbeat while it does, so no fixed deadline cuts an active
                    // preparation short. Only a silent host times out.
                    if (StagingAlive())
                    {
                        waitDeadline = 0;
                        return;
                    }
                    if (waitDeadline <= 0) waitDeadline = now + batch.timeoutSeconds;
                    if (now < waitDeadline) return;
                    WriteReceipt(nextEntry.index, "failed", "The staged package did not complete in time.");
                    Finish();
                    return;
                }
                waitDeadline = 0;
                claimAt = now;
                claimFromThisDomain = true;
                inFlightPackage = nextEntry.package;
                WriteJson(Path.Combine(DirectoryPath, "batch-active.json"),
                    JsonUtility.ToJson(new Claim { index = nextEntry.index, package = nextEntry.package }));
                try { AssetDatabase.ImportPackage(nextEntry.package, false); }
                catch (Exception error)
                {
                    OnPackageEvent(nextEntry.package, "failed", error.Message);
                }
                // ImportPackage returning is NOT the completion signal; the
                // callbacks own the result, including imports that reload scripts.
            }
            catch (Exception error)
            {
                Debug.LogError("Unity Asset Library batch runner: " + error.Message);
                nextPoll = now + 5;
            }
        }

        private static bool StagingAlive()
        {
            try
            {
                if (batch.stagingHeartbeat == null) return false;
                return (DateTime.UtcNow - File.GetLastWriteTimeUtc(batch.stagingHeartbeat)).TotalSeconds
                    < batch.stagingHeartbeatMaxAgeSeconds;
            }
            catch (IOException) { return false; }
            catch (UnauthorizedAccessException) { return false; }
        }

        private static BatchReceipt ReadReceipt(int index)
        {
            return Read<BatchReceipt>(Path.Combine("batch-receipts", index + ".json"));
        }

        private static void OnPackageEvent(string name, string status, string error)
        {
            Claim claim = Read<Claim>("batch-active.json");
            string expected = claim != null ? claim.package : inFlightPackage;
            if (expected == null || !string.Equals(Path.GetFileNameWithoutExtension(name),
                Path.GetFileNameWithoutExtension(expected), StringComparison.OrdinalIgnoreCase)) return;
            int index = claim != null ? claim.index : -1;
            if (index < 0) return;
            inFlightPackage = null;
            // Commit the receipt before removing the claim; a crash between the
            // two is recovered by the Tick claim check without replaying.
            WriteReceipt(index, status, error);
            File.Delete(Path.Combine(DirectoryPath, "batch-active.json"));
            claimFromThisDomain = false;
            settleUntil = EditorApplication.timeSinceStartup + 1;
            waitDeadline = 0;
        }

        private static void CancelRemaining()
        {
            foreach (BatchEntry entry in batch.packages)
            {
                if (!HasReceipt(entry.index)) WriteReceipt(entry.index, "cancelled", null);
            }
            Finish();
        }

        private static void Finish()
        {
            bool anyFailed = false;
            BatchReceipt[] statuses = new BatchReceipt[batch.packages.Length];
            for (int i = 0; i < batch.packages.Length; i++)
            {
                BatchReceipt receipt = ReadReceipt(batch.packages[i].index)
                    ?? new BatchReceipt { index = batch.packages[i].index, status = "cancelled" };
                if (receipt.status == "failed") anyFailed = true;
                statuses[i] = receipt;
            }
            WriteJson(Path.Combine(DirectoryPath, "batch-result.json"),
                JsonUtility.ToJson(new BatchResult { id = batch.id, statuses = statuses }));
            finishing = true;
            exitCode = anyFailed ? 1 : 0;
            // The callback runs before domain reload unloads this removed assembly.
            UnityEditor.Compilation.CompilationPipeline.compilationFinished += _ => EditorApplication.Exit(exitCode);
            AssetDatabase.StartAssetEditing();
            try
            {
                const string folder = "Assets/UnityAssetLibraryBatch";
                AssetDatabase.DeleteAsset(folder + "/UnityAssetLibraryBatchRunner.cs");
                AssetDatabase.DeleteAsset(folder + "/UnityAssetLibraryBatchRunner.asmdef");
                string absoluteFolder = Path.Combine(Application.dataPath, "UnityAssetLibraryBatch");
                if (Directory.Exists(absoluteFolder) && Directory.GetFileSystemEntries(absoluteFolder).Length == 0)
                    AssetDatabase.DeleteAsset(folder);
            }
            finally
            {
                AssetDatabase.StopAssetEditing();
            }
            AssetDatabase.Refresh();
            UnityEditor.Compilation.CompilationPipeline.RequestScriptCompilation();
        }

        private static bool IsSafePackagePath(string path)
        {
            return !string.IsNullOrEmpty(path) && Path.IsPathRooted(path) &&
                path.EndsWith(".unitypackage", StringComparison.OrdinalIgnoreCase);
        }

        private static T Read<T>(string name) where T : class
        {
            try
            {
                string text = File.ReadAllText(Path.Combine(DirectoryPath, name));
                return JsonUtility.FromJson<T>(text);
            }
            catch (IOException) { return null; }
            catch (UnauthorizedAccessException) { return null; }
        }

        private static void WriteJson(string path, string text)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string temp = path + ".tmp";
            File.WriteAllText(temp, text);
            // The FIRST write to a receipt has no destination to replace yet.
            if (File.Exists(path)) File.Replace(temp, path, null);
            else File.Move(temp, path);
        }

        [Serializable] private class BatchEntry { public int index; public string package; }
        [Serializable] private class BatchRequest { public string id; public string stop; public string stagingHeartbeat; public int stagingHeartbeatMaxAgeSeconds; public int timeoutSeconds; public BatchEntry[] packages; }
        [Serializable] private class BatchReceipt { public int index; public string status; public string error; }
        [Serializable] private class BatchResult { public string id; public BatchReceipt[] statuses; }
        [Serializable] private class Claim { public int index; public string package; }
    }
}
