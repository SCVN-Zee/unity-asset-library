"""Install legacy Unity packages as files, not as Editor operations.

Preflight is read-only. Each file is committed atomically, but a package is not
atomic to a running Editor. Save Editor changes first; Unity refreshes separately.
Incomplete transactions retain originals and a journal outside Assets and block
subsequent installs until inspected. No unconfirmed operation is replayed.
"""
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import uuid
import unicodedata

CHUNK = 1 << 20
BACKUPS = ".ual-import-backups"
GUID = re.compile(r"^guid:[ \t]*([0-9a-fA-F]{32})[ \t\r]*$", re.MULTILINE)
FOLDER = re.compile(r"^folderAsset:[ \t]*yes[ \t\r]*$", re.MULTILINE)
NOFOLLOW = os.O_NOFOLLOW


class InstallError(Exception):
    def __init__(self, message, backup_path=None):
        super().__init__(message)
        self.backup_path = backup_path


def _guid(data):
    matches = GUID.findall(data.decode("utf-8", errors="strict"))
    if len(matches) != 1:
        raise InstallError("Metadata must declare exactly one GUID")
    return matches[0].lower()


def _key(path):
    return unicodedata.normalize("NFC", path).casefold()


def _path(value):
    parts = value.split("/")
    if (len(parts) < 2 or parts[0] != "Assets" or "\\" in value or
            any(ord(c) < 32 for c in value) or
            any(p in ("", ".", "..") or p.endswith((" ", ".")) for p in parts) or
            any(p.lower().endswith(".meta") for p in parts)):
        raise InstallError("Unsafe or unsupported asset pathname: " + repr(value))
    return value


def parse_package(package, staging):
    """Stream payloads into private local staging; never extract archive paths."""
    records, seen = {}, set()
    # GzipFile-backed mode handles Unity gzip FEXTRA headers (tarfile stream
    # mode in Python 3.12 misreads them). Payload copying is still bounded.
    with tarfile.open(package, "r:*") as archive:
        for member in archive:
            name = member.name
            if name.startswith("./"):
                name = name[2:]
            parts = name.rstrip("/").split("/")
            if name.startswith("/") or "\\" in name or any(p in ("", ".", "..") for p in parts):
                if member.isdir() and name in ("", "."):
                    continue
                raise InstallError("Unsafe archive member: " + repr(member.name))
            if member.isdir():
                if len(parts) != 1 or not re.fullmatch(r"[0-9a-fA-F]{32}", parts[0]):
                    raise InstallError("Unsupported archive directory: " + name)
                continue
            if not member.isfile():
                raise InstallError("Archive links and special files are not allowed: " + name)
            if len(parts) == 1 and parts[0] == ".icon.png":
                continue  # Store preview, not an asset.
            if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{32}", parts[0]):
                raise InstallError("Unsupported archive member: " + name)
            guid, leaf = parts[0].lower(), parts[1]
            if leaf not in ("pathname", "asset", "asset.meta", "preview.png", ".icon.png"):
                raise InstallError("Unsupported archive member: " + name)
            key = (guid, leaf)
            if key in seen:
                raise InstallError("Duplicate archive member: " + name)
            seen.add(key)
            if leaf in ("preview.png", ".icon.png"):
                continue
            record = records.setdefault(guid, {"guid": guid})
            with archive.extractfile(member) as source:
                if leaf == "asset":
                    destination = os.path.join(staging, guid + ".asset")
                    with open(destination, "xb") as output:
                        shutil.copyfileobj(source, output, CHUNK)
                    record["asset"] = destination
                else:
                    limit = 16384 if leaf == "pathname" else 16 * CHUNK
                    if member.size > limit:
                        raise InstallError("Package metadata is too large: " + name)
                    record[leaf] = source.read(limit + 1)
    paths = set()
    for record in records.values():
        if "pathname" not in record or "asset.meta" not in record:
            raise InstallError("Package asset lacks pathname or metadata: " + record["guid"])
        if _guid(record["asset.meta"]) != record["guid"]:
            raise InstallError("Package GUID does not match its metadata: " + record["guid"])
        # Unity writes a pathname line followed by an optional ASCII 00 trailer.
        pathname, _, trailer = record["pathname"].partition(b"\n")
        if trailer not in (b"", b"00", b"00\n", b"00\r\n"):
            raise InstallError("Malformed pathname trailer: " + record["guid"])
        record["path"] = _path(pathname.rstrip(b"\r").decode("utf-8"))
        folded = _key(record["path"])
        if folded in paths:
            raise InstallError("Duplicate or case-colliding package path: " + record["path"])
        paths.add(folded)
        record["folder"] = bool(FOLDER.search(record["asset.meta"].decode("utf-8")))
        if record["folder"]:
            if "asset" in record and os.path.getsize(record["asset"]):
                raise InstallError("Folder asset unexpectedly contains file data: " + record["path"])
        elif "asset" not in record:
            raise InstallError("Package file lacks asset content: " + record["path"])
        meta_file = os.path.join(staging, record["guid"] + ".meta")
        with open(meta_file, "xb") as output:
            output.write(record["asset.meta"])
        record["meta_file"] = meta_file
    if not records:
        raise InstallError("Package contains no assets")
    return list(records.values())


@contextlib.contextmanager
def _parent(root, rel):
    """Pin every ancestor; no reads or writes follow a project symlink."""
    parts = rel.split("/")
    fd = os.dup(root)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd, parts[-1]
    finally:
        os.close(fd)


def _digest(stream):
    result = hashlib.sha256()
    for chunk in iter(lambda: stream.read(CHUNK), b""):
        result.update(chunk)
    return result.hexdigest()


def _snapshot(root, rel):
    try:
        with _parent(root, rel) as (parent, name):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                return {"kind": "dir", "dev": info.st_dev, "ino": info.st_ino}
            if not stat.S_ISREG(info.st_mode):
                raise InstallError("Asset path is a link or special file: " + rel)
            with os.fdopen(os.open(name, os.O_RDONLY | NOFOLLOW, dir_fd=parent), "rb") as stream:
                before = os.fstat(stream.fileno())
                digest = _digest(stream)
                after = os.fstat(stream.fileno())
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise InstallError("File changed while inspecting: " + rel)
            return {"kind": "file", "sha": digest, "mode": stat.S_IMODE(after.st_mode),
                    "dev": after.st_dev, "ino": after.st_ino, "size": after.st_size, "mtime": after.st_mtime_ns}
    except FileNotFoundError:
        return None


def _read(root, rel):
    with _parent(root, rel) as (parent, name):
        with os.fdopen(os.open(name, os.O_RDONLY | NOFOLLOW, dir_fd=parent), "rb") as stream:
            return stream.read(16 * CHUNK + 1)


def _scan(root):
    identities, paths = {}, {}
    def fail(error):
        raise error
    # fwalk pins directories for metadata reads and never descends symlinks.
    for current, dirs, files, fd in os.fwalk("Assets", dir_fd=root, follow_symlinks=False, onerror=fail):
        for name in dirs + files:
            rel = current + "/" + name
            key = _key(rel)
            if key in paths and paths[key] != rel:
                raise InstallError("Existing case-colliding asset paths: " + rel)
            paths[key] = rel
        for name in files:
            if not name.endswith(".meta"):
                continue
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                continue  # A target touching this link is rejected by preflight.
            with os.fdopen(os.open(name, os.O_RDONLY | NOFOLLOW, dir_fd=fd), "rb") as stream:
                data = stream.read(16 * CHUNK + 1)
            try:
                guid = _guid(data)
            except (InstallError, UnicodeError):
                continue  # Malformed unrelated metadata must not block new assets.
            rel = current + "/" + name[:-5]
            if guid in identities and identities[guid] != rel:
                raise InstallError("Duplicate GUID already in project: " + guid)
            identities[guid] = rel
    return identities, paths


def _plan(root, records, overwrite):
    identities, paths = _scan(root)
    folders = {r["path"]: identities[r["guid"]] for r in records if r["folder"] and r["guid"] in identities}
    checks, operations, directories, targets = {}, [], set(), set()

    def inspect(rel):
        if rel not in checks:
            checks[rel] = _snapshot(root, rel)
        return checks[rel]

    for record in sorted(records, key=lambda r: (not r["folder"], r["path"])):
        target = identities.get(record["guid"])
        if target is None:
            parents = [p for p in folders if record["path"].startswith(p + "/")]
            parent = max(parents, key=len) if parents else None
            target = folders[parent] + record["path"][len(parent):] if parent else record["path"]
        _path(target)
        if _key(target) in targets:
            raise InstallError("Package assets resolve to the same destination: " + target)
        targets.add(_key(target))
        parts = target.split("/")
        for i in range(1, len(parts) + 1):
            rel = "/".join(parts[:i])
            collision = paths.get(_key(rel))
            if collision is not None and collision != rel:
                raise InstallError("Asset path has different casing: " + rel + " vs " + collision)
            paths[_key(rel)] = rel
            if i < len(parts) or record["folder"]:
                state = inspect(rel)
                if state is not None and state["kind"] != "dir":
                    raise InstallError("Asset parent is not a directory: " + rel)
                if state is None:
                    directories.add(rel)
        existing, meta = inspect(target), inspect(target + ".meta")
        if existing is not None and (existing["kind"] == "dir") != record["folder"]:
            raise InstallError("Asset changes file/folder type: " + target)
        if existing is not None or meta is not None:
            if meta is None or meta["kind"] != "file" or _guid(_read(root, target + ".meta")) != record["guid"]:
                raise InstallError("Destination is occupied by a different or unknown GUID: " + target)
        files = [(target + ".meta", record["meta_file"])]
        if not record["folder"]:
            files.append((target, record["asset"]))
        for rel, source in files:
            collision = paths.get(_key(rel))
            if collision is not None and collision != rel:
                raise InstallError("Asset metadata has different casing: " + rel)
            previous = inspect(rel)
            with open(source, "rb") as stream:
                digest = _digest(stream)
            if previous and previous.get("sha") == digest:
                continue
            if previous and not overwrite:
                raise InstallError("Existing asset differs: " + rel + ". Enable replacement with backups to update it.")
            operations.append({"rel": rel, "source": source, "old": previous, "sha": digest})
    # Detect file/parent and asset/metadata collisions before creating anything.
    file_paths = {_key(op["rel"]) for op in operations}
    if file_paths & {_key(p) for p in directories}:
        raise InstallError("Package contains a file/directory collision")
    return operations, sorted(directories, key=lambda p: (p.count("/"), p)), checks


def _journal(root, txn, data):
    with _parent(root, txn + "/journal.json") as (parent, name):
        temp = ".journal-" + uuid.uuid4().hex
        try:
            with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600, dir_fd=parent), "w") as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp, dir_fd=parent)


def _check_recovery(root, project):
    if _snapshot(root, BACKUPS) is None:
        return
    fd = os.open(BACKUPS, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW, dir_fd=root)
    try:
        for name in os.listdir(fd):
            location = BACKUPS + "/" + name
            try:
                data = json.loads(_read(root, location + "/journal.json"))
                if not isinstance(data, dict) or data.get("status") not in ("complete", "rolled_back"):
                    raise ValueError("unresolved")
            except (OSError, ValueError, InstallError):
                raise InstallError("An earlier installation needs inspection before retrying: " + os.path.join(project, location), os.path.join(project, location))
    finally:
        os.close(fd)


def _put(root, rel, source, expected, mode=0o644):
    """Atomic per-file commit. External editors do not share our lock; recheck
    immediately before commit. New files use an exclusive link, never overwrite."""
    with _parent(root, rel) as (parent, name):
        temp = ".ual-import-" + uuid.uuid4().hex
        committed = None
        try:
            source_context = contextlib.nullcontext(source) if hasattr(source, "read") else open(source, "rb")
            with source_context as src, os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, mode, dir_fd=parent), "wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK)
                dst.flush()
                os.fchmod(dst.fileno(), mode)
                os.fsync(dst.fileno())
                info = os.fstat(dst.fileno())
                identity = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
            if _snapshot(root, rel) != expected:
                raise InstallError("File changed during installation: " + rel)
            if expected is None:
                os.link(temp, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                committed = identity
                os.unlink(temp, dir_fd=parent)
            else:
                os.replace(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
                committed = identity
            os.fsync(parent)
            return committed
        except Exception as error:
            error.installed_identity = committed
            raise
        finally:
            with contextlib.suppress(OSError):
                os.unlink(temp, dir_fd=parent)


def _apply(root, project, operations, directories, checks):
    if not operations and not directories:
        return None
    with contextlib.suppress(FileExistsError):
        os.mkdir(BACKUPS, 0o700, dir_fd=root)
    os.fsync(root)
    backup_fd = os.open(BACKUPS, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW, dir_fd=root)
    txn = BACKUPS + "/" + uuid.uuid4().hex
    try:
        os.mkdir(txn.split("/")[-1], 0o700, dir_fd=backup_fd)
        os.fsync(backup_fd)
    finally:
        os.close(backup_fd)
    absolute = os.path.join(project, txn)
    journal = {"status": "preparing", "directories": directories, "ops": []}
    _journal(root, txn, journal)
    attempted, created = [], []
    try:
        for i, op in enumerate(operations):
            item = {k: op[k] for k in ("rel", "old", "sha")}
            item["backup"] = str(i) if op["old"] else None
            op["backup"] = item["backup"]
            if item["backup"]:
                backup_rel = txn + "/" + item["backup"]
                with _parent(root, op["rel"]) as (parent, name), _parent(root, backup_rel) as (dest_parent, dest_name):
                    with os.fdopen(os.open(name, os.O_RDONLY | NOFOLLOW, dir_fd=parent), "rb") as src, os.fdopen(os.open(dest_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600, dir_fd=dest_parent), "wb") as dst:
                        shutil.copyfileobj(src, dst, CHUNK)
                        dst.flush()
                        os.fsync(dst.fileno())
                if _snapshot(root, backup_rel)["sha"] != op["old"]["sha"]:
                    raise InstallError("File changed before backup: " + op["rel"])
            journal["ops"].append(item)
        for rel, expected in checks.items():
            if _snapshot(root, rel) != expected:
                raise InstallError("Project changed during preflight: " + rel)
        journal["status"] = "installing"
        _journal(root, txn, journal)
        for rel in directories:
            with _parent(root, rel) as (parent, name):
                os.mkdir(name, dir_fd=parent)
                created.append(rel)
                os.fsync(parent)
        for op in operations:
            try:
                identity = _put(root, op["rel"], op["source"], op["old"], op["old"]["mode"] if op["old"] else 0o644)
            except Exception as failure:
                if getattr(failure, "installed_identity", None):
                    attempted.append((op, failure.installed_identity))
                raise
            attempted.append((op, identity))
        journal["status"] = "complete"
        _journal(root, txn, journal)
    except Exception as error:
        recovery = []
        for op, identity in reversed(attempted):
            try:
                current = _snapshot(root, op["rel"])
                if current == op["old"]:
                    continue
                if current is None or current.get("sha") != op["sha"] or tuple(current.get(k) for k in ("dev", "ino", "mtime", "size")) != identity:
                    recovery.append(op["rel"])
                    continue
                if op["old"]:
                    with _parent(root, txn + "/" + op["backup"]) as (parent, name):
                        with os.fdopen(os.open(name, os.O_RDONLY | NOFOLLOW, dir_fd=parent), "rb") as original:
                            if _digest(original) != op["old"]["sha"]:
                                raise InstallError("Backup changed before recovery: " + op["rel"])
                            original.seek(0)
                            _put(root, op["rel"], original, current, op["old"]["mode"])
                else:
                    with _parent(root, op["rel"]) as (parent, name):
                        os.unlink(name, dir_fd=parent)
                        os.fsync(parent)
            except Exception:
                recovery.append(op["rel"])
        for rel in reversed(created):
            try:
                with _parent(root, rel) as (parent, name):
                    os.rmdir(name, dir_fd=parent)
                    os.fsync(parent)
            except OSError:
                recovery.append(rel)
        journal["status"] = "needs_recovery" if recovery else "rolled_back"
        journal["unresolved"] = recovery
        try:
            _journal(root, txn, journal)
        except OSError:
            pass  # The durable installing/preparing journal still blocks retries.
        raise InstallError(str(error) + ("; recovery required" if recovery else "; package changes rolled back") + ". Journal/backups: " + absolute, absolute) from error
    if any(op["old"] for op in operations):
        return absolute
    try:
        shutil.rmtree(absolute)
    except OSError:
        return absolute  # Committed files are successful even if journal cleanup fails.
    return None


def install_package(project, package_path, overwrite, staging):
    with contextlib.ExitStack() as stack:
        root = os.open(project, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW)
        stack.callback(os.close, root)
        assets = os.open("Assets", os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW, dir_fd=root)
        stack.callback(os.close, assets)
        _check_recovery(root, project)
        records = parse_package(package_path, staging)
        operations, directories, checks = _plan(root, records, overwrite)
        return _apply(root, project, operations, directories, checks)
