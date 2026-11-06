"""Package public application sources only; runtime secrets stay on the target host."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile

PATTERNS=('Makefile','.env.example','deploy/*.yml','deploy/librechat.yaml','deploy/upstream/docker-compose.yml',
          'deploy/upstream/LICENSE','deploy/upstream/lock.json','deploy/proxy/Caddyfile',
          'deploy/branding/Dockerfile','deploy/branding/.dockerignore','deploy/branding/*.mjs','deploy/branding/*.svg',
          'services/platform-api/Dockerfile','services/platform-api/.dockerignore','services/platform-api/*.cjs','services/platform-api/*.mjs',
          'services/admin-panel/Dockerfile','services/admin-panel/.dockerignore','services/admin-panel/*.mjs','services/admin-panel/overlays/*',
          'services/metadata-worker/*.py','services/metadata-worker/requirements.txt',
          'scripts/*.py','scripts/*.cjs','scripts/*.sh','deploy/*.sh','deploy/demo/*','services/clickhouse-mcp/*.py','services/clickhouse-mcp/Dockerfile',
          'services/clickhouse-mcp/.dockerignore','services/clickhouse-mcp/requirements.txt','db/*.sql','db/migrations/*.sql')
REQUIRED={'Makefile','deploy/librechat.yaml','deploy/docker-compose.override.yml','deploy/upstream/docker-compose.yml'}
MAX_BYTES=16*1024*1024
MAX_FILES=500


def allowed(name):
    parts=PurePosixPath(name).parts
    if name!=PurePosixPath(name).as_posix() or not re.fullmatch(r'[A-Za-z0-9_./-]+',name) or name.startswith('/') or any(p in ('.','..') for p in name.split('/')):
        return False
    # Match globs at their exact depth: '*' must never include a private subdirectory.
    return any(len(parts)==len(PurePosixPath(pattern).parts) and PurePosixPath(name).match(pattern) for pattern in PATTERNS)


def package(source,output):
    source=Path(source).resolve();files={}
    for pattern in PATTERNS:
        for path in source.glob(pattern):
            name=path.relative_to(source).as_posix()
            if not allowed(name):raise ValueError('Disallowed release source path')
            for parent in [path,*path.parents]:
                if parent==source:break
                if parent.is_symlink():raise ValueError('Release sources must not contain symlinks')
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('Release sources must be unlinked regular files')
            if path.stat().st_size>MAX_BYTES:raise ValueError('Release sources exceed size limit')
            files[name]=path.read_bytes()
    if not REQUIRED.issubset(files) or len(files)>MAX_FILES or sum(map(len,files.values()))>MAX_BYTES:
        raise ValueError('Release sources are incomplete or exceed limits')
    manifest={'version':1,'files':{name:hashlib.sha256(data).hexdigest() for name,data in sorted(files.items())}}
    files['release.json']=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    buffer=io.BytesIO()
    with gzip.GzipFile(fileobj=buffer,mode='wb',filename='',mtime=0) as zipped:
        with tarfile.open(fileobj=zipped,mode='w',format=tarfile.USTAR_FORMAT) as archive:
            for name,data in sorted(files.items()):
                entry=tarfile.TarInfo(name);entry.size=len(data);entry.mode=0o644;entry.mtime=0
                archive.addfile(entry,io.BytesIO(data))
    data=buffer.getvalue()
    descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'wb') as destination:destination.write(data)
    return hashlib.sha256(data).hexdigest()


def verify(path):
    path=Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size>MAX_BYTES:
        raise ValueError('Invalid release archive')
    files={};size=0
    with tarfile.open(path,'r:gz') as archive:
        for entry in archive:
            size+=entry.size
            if (not entry.isfile() or entry.mode!=0o644 or entry.uid!=0 or entry.gid!=0 or entry.name in files or entry.size<0 or
                    size>MAX_BYTES or len(files)>MAX_FILES or (entry.name!='release.json' and not allowed(entry.name))):
                raise ValueError('Unsafe release archive member')
            files[entry.name]=archive.extractfile(entry).read()
    try:
        manifest=json.loads(files.pop('release.json'))
        actual={name:hashlib.sha256(data).hexdigest() for name,data in sorted(files.items())}
        if manifest!={'version':1,'files':actual} or not REQUIRED.issubset(files):raise ValueError()
    except (ValueError,KeyError,TypeError):
        raise ValueError('Release manifest does not match its sources') from None
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--verify',type=Path)
    args=parser.parse_args()
    if bool(args.output)==bool(args.verify):parser.error('Choose exactly one of --output or --verify')
    try:
        digest=verify(args.verify) if args.verify else package(Path(__file__).resolve().parents[1],args.output)
        print(json.dumps({'sha256':digest}))
    except (OSError,ValueError,tarfile.TarError):
        parser.exit(1,'Release packaging/verification refused; check paths, archive and source permissions.\n')
