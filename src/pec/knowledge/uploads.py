"""Stage one upload request before replacing any source files under the writer lock."""
import logging
import os
from pathlib import Path
import shutil
import stat
import tempfile
from io import BytesIO

logger = logging.getLogger(__name__)
MAX_BATCH_FILES = 2000
MAX_BATCH_BYTES = 500 * 1024 * 1024


class UploadLimitError(ValueError):
    """An upload exceeds a configured file or batch limit."""


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None:
        return is_junction()
    if os.name != "nt":
        return False
    try:
        attributes = path.lstat().st_file_attributes
    except FileNotFoundError:
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


class SourceUploadBatch:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = None
        self.entries = []
        self.seen = set()
        self.size = 0
        self.retain = False

    def __enter__(self):
        self.directory = Path(tempfile.mkdtemp(prefix='.pec-upload-', dir=self.root))
        if self.directory.resolve().parent != self.root:
            raise ValueError('上传暂存目录越界')
        return self

    def target(self, relative):
        target = self.root / relative
        if not target.resolve().is_relative_to(self.root) or target == self.root:
            raise ValueError('上传目标路径越界')
        if any(
            _is_link_or_junction(p)
            for p in (target, *target.parents)
            if p != self.root and self.root in p.parents
        ):
            raise ValueError('上传目标不能包含链接目录或文件')
        if target.exists() and not target.is_file():
            raise ValueError('上传目标不是文件')
        return target

    def add(self, relative, data):
        return self.add_stream(relative, BytesIO(data))

    def add_stream(self, relative, stream, *, max_bytes=None):
        target = self.target(relative)
        key = str(target.resolve()).casefold()
        if key in self.seen:
            raise ValueError('同一批上传包含重复目标文件，请分别上传')
        if len(self.entries) >= MAX_BATCH_FILES:
            raise UploadLimitError('每批资料最多 2000 个文件、总量 500 MB')
        staged = self.directory / f'{len(self.entries)}.upload'
        size = 0
        with staged.open('wb') as output:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if (
                    (max_bytes is not None and size > max_bytes)
                    or self.size + size > MAX_BATCH_BYTES
                ):
                    raise UploadLimitError('每批资料最多 2000 个文件、总量 500 MB')
                output.write(chunk)
        self.entries.append((relative, staged))
        self.seen.add(key)
        self.size += size
        return relative

    def commit(self):
        prepared = []
        # Complete all validation and backup copies before the first replacement.
        for i, (relative, staged) in enumerate(self.entries):
            target = self.target(relative)
            backup = self.directory / f'{i}.backup' if target.exists() else None
            if backup is not None:
                shutil.copy2(target, backup)
            target.parent.mkdir(parents=True, exist_ok=True)
            prepared.append((target, staged, backup))
        written = []
        try:
            for target, staged, backup in prepared:
                os.replace(staged, target)
                written.append((target, backup))
        except OSError:
            for target, backup in reversed(written):
                try:
                    if backup is None:
                        target.unlink(missing_ok=True)
                    else:
                        os.replace(backup, target)
                except OSError:
                    self.retain = True
                    logger.exception('Upload rollback failed; preserve backup directory %s', self.directory)
            raise

    def __exit__(self, *args):
        # Retain recovery copies if storage failure also prevents rollback.
        if not self.retain and self.directory.resolve().parent == self.root:
            try:
                shutil.rmtree(self.directory)
            except OSError:
                logger.warning('Upload staging directory could not be removed: %s', self.directory)
