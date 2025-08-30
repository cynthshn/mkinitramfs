#!/usr/bin/python3

import datetime
import errno
import glob
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

def save(no_cleanup, yes):
    if os.geteuid() != 0:
        program = os.path.basename(sys.argv[0])
        raise PermissionError(
              errno.EACCES, 'Please use sudo or run the script as root.')
    base, snapshots = get_snapshots()
    snapshots_with_index = [(idx, fn)
                            for idx, fn in snapshots if idx is not None]
    if snapshots_with_index:
        max_idx, fn = snapshots_with_index[-1]
        if max_idx < 0 or max_idx > 27:
            raise ValueError(f'.sb file index "{max_idx}" '
                             'out of range (1-28), no changes made.')
        new_idx = max_idx + 1
    else:
        new_idx = 1
    fn = '{:02}-{:%Y%m%dT%H%M%S}.sb'.format(new_idx,
                            datetime.datetime.now())
    output = f'{base}/{fn}'
    if not yes:
        yes = prompt(f'Create snapshot "{fn}"? [y/N]:')
    if not yes:
        return
    try:
        st = os.stat(base)
    except FileNotFoundError:
        os.makedirs(base, exist_ok=False)
    else:
        if not stat.S_ISDIR(st.st_mode):
            raise NotADirectoryError(errno.errno.ENOTDIR,
                  f'{base} is not a directory')
    regex_exclude = re.compile('|'.join(EXCLUDE.split('\n')))
    temp_dir_holder = smart_temporary_directory()
    temp_dir = next(temp_dir_holder).rstrip('/')
    out = System('while read FILE; do\n'
                 f'cp --parents -afr "$FILE" "{temp_dir}"\ndone',
                 cwd='/run/initramfs/memory/changes')
    args = ['find', '(', '-type', 'd', '-printf', '%p/\n', ',',
            '-not', '-type', 'd', '-print', ')']
    with subprocess.Popen(args,
         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
         cwd='/run/initramfs/memory/changes') as proc:
        for _row in proc.stdout.readlines():
            row = _row.decode()
            if not row.startswith('./'):
                continue
            row = row[2:]
            if row.endswith('\r\n'):
                row = row[:-2]
            elif row.endswith('\n'):
                row = row[:-1]
            if not regex_exclude.search(row):
                out.write((row+'\n').encode())
        status = proc.wait()
        if status != 0:
            raise OSError(status, 'unable to list changes')
        status = out.close()
        if status != 0:
            raise OSError(status, 'unable to save changes')
    if not no_cleanup:
        cleanup(base=temp_dir, ignore_without=True)
    with tempfile.NamedTemporaryFile() as f:
        args = ['mksquashfs', temp_dir, f.name, '-comp', 'xz',
                '-b', '1024K', '-Xbcj', 'x86',
                '-always-use-fragments', '-noappend']
        subprocess.run(args, check=True)
        shutil.copyfile(f.name, output)

def print_snapshots():
    for idx, fn in get_snapshots()[1]:
        print (fn)

def rollback(yes):
    if os.geteuid() != 0:
        program = os.path.basename(sys.argv[0])
        raise PermissionError(
              errno.EACCES, 'Please use sudo or run the script as root.')
    base, snapshots = get_snapshots()
    snapshots_with_index = [(idx, fn)
                            for idx, fn in snapshots if idx is not None]
    if snapshots_with_index:
        idx, fn = max(snapshots_with_index)
    elif snapshots:
        idx, fn = snapshots[-1]
    else:
        raise FileNotFoundError(errno.ENOENT, 'no snapshot found')
    if not yes:
        yes = prompt(f'\033[0;31mDelete snapshot "{fn}"?\033[0m [y/N]:')
    if yes:
        os.remove(f'{base}/{fn}')

def pack(output, yes):
    if os.geteuid() != 0:
        program = os.path.basename(sys.argv[0])
        raise PermissionError(
              errno.EACCES, 'Please use sudo or run the script as root.')
    if os.path.exists(output):
        raise FileExistsError(errno.EEXIST, f'File "{output}" exists')
    if not yes:
        print ('\033[0;31mWarning: Creating the system image will cause '
               'all caches and some configuration files on your system '
               'to be deleted.\033[0m')
        yes = prompt('Continue? [y/N]:')
    if not yes:
        return
    corefs = []
    for mod in ('bin etc home lib lib64 libx32 opt root sbin '
                'srv usr var').split():
        if os.path.exists(f'/{mod}'):
            corefs.append(f'/{mod}')
    for _ in range(5):
        os.system('killall systemd-journald')
        time.sleep(.5)
    cleanup(base='/', ignore_without=False)
    args = ['mksquashfs'] + corefs + [output] \
         + ['-comp', 'xz', '-b', '1024K', '-Xbcj', 'x86',
            '-always-use-fragments', '-keep-as-directory']
    sys.exit(subprocess.run(args))

def export(output):
    if os.geteuid() != 0:
        program = os.path.basename(sys.argv[0])
        raise PermissionError(
              errno.EACCES, 'Please use sudo or run the script as root.')
    if os.path.exists(output):
        raise FileExistsError(errno.EEXIST, f'File "{output}" exists')
    base = os.path.abspath(output)
    static = os.path.join(base, 'assets')
    os.makedirs(static, mode=0o755, exist_ok=True)
    for fn in ('dir2sb init.in initramfs_pack initramfs_unpack rmsbdir '
               'sb sb2dir').split():
        shutil.copyfile(f'/run/initramfs/usr/share/fresh_os/{fn}',
                        os.path.join(static, fn))
    for fn in ('blkid busybox eject micropython').split():
        shutil.copyfile(f'/run/initramfs/bin/{fn}',
                        os.path.join(static, fn))
    shutil.copyfile(
        '/run/initramfs/shutdown',
        os.path.join(static, 'shutdown')
    )
    shutil.copyfile(
        '/run/initramfs/usr/lib/micropython/bootstraplib.py',
        os.path.join(base, 'bootstraplib.py')
    )
    shutil.copyfile(
        '/run/initramfs/usr/share/fresh_os/initramfs_create.py',
        os.path.join(base, 'initramfs_create.py')
    )
    shutil.copyfile(
        '/run/initramfs/usr/share/fresh_os/savechanges',
        os.path.join(base, 'savechanges.py')
    )

class System:

    def __init__(self, cmd, cwd=None):
        in_, self.out = os.pipe()
        self.pid = os.fork()
        if 0 == self.pid:
            os.close(self.out)
            os.dup2(in_, 0)
            os.close(in_)
            if cwd is not None:
                os.chdir(cwd)
            os.system(cmd)
            os._exit(0)
        os.close(in_)

    def write(self, b):
        os.write(self.out, b)

    def close(self):
        os.close(self.out)
        return os.waitpid(self.pid, 0)[1]

def get_snapshots():

    import json

    with open('/run/initramfs/memory/arguments') as f:
        data = f.read()
    args = json.loads(data)
    base = f'/run/initramfs{args["home"]}/snapshots'
    snapshots = []
    try:
        files = sorted(os.listdir(base))
    except FileNotFoundError:
        return base, snapshots
    for fn in files:
        root, ext = os.path.splitext(fn)
        if ext.lower() != '.sb':
            continue
        if not os.path.isfile(f'{base}/{fn}'):
            continue
        try:
            idx = int(root.split('-', 1)[0])
        except ValueError:
            snapshots.append((None, fn))
        else:
            snapshots.append((idx, fn))
    return base, snapshots

def cleanup(base='/', ignore_without=False):
    files = []
    recursives = []
    for row in CLEANUP.split('\n'):
        row = row.lstrip()
        if not row or row.startswith('#'):
            continue
        row = row.lstrip('/')
        if row.endswith('/'):
            row = row.rstrip('/')
            if '*' in row:
                recursives.extend(glob.glob(
                           os.path.join(base, row), recursive=True))
            else:
                recursives.append(os.path.join(base, row))
        else:
            if '*' in row:
                files.extend(glob.glob(
                      os.path.join(base, row), recursive=True))
            else:
                files.append(os.path.join(base, row))
    for path in sorted(files):
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            continue
        if stat.S_ISREG(st.st_mode):
            os.remove(path)
        elif not ignore_without \
             and stat.S_ISCHR(st.st_mode) and 0 == st.st_rdev:
            os.remove(path)
    for path in sorted(recursives):
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            continue
        if stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            os.remove(path)
        elif stat.S_ISDIR(st.st_mode):
            shutil.rmtree(path)
        elif not ignore_without \
             and stat.S_ISCHR(st.st_mode) and 0 == st.st_rdev:
            os.remove(path)

def prompt(hint, default=False):
    while True:
        try:
            _choice = input(hint)
        except KeyboardInterrupt:
            return default
        choice = _choice.strip().lower()
        if choice in ['', 'q', 'quit', 'exit']:
            return default
        elif choice in ['y', 'yes']:
            return True
        elif choice in ['n', 'no']:
            return False
        else:
            print (f'Option "{_choice}" not recognized.')
            continue

def smart_temporary_directory():
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir

def main():

    import argparse

    parser = argparse.ArgumentParser()
    group = parser.add_argument_group('default',
            'Create a snapshot of the current system. '
            'This is the default behavior.')
    group.add_argument(
               '--no-cleanup',
            dest='no_cleanup',
            help='Do not perform cleanup',
          action='store_true'
    )
    group.add_argument(
                '-y',
               '--yes',
            dest='yes',
            help='Automatic yes to prompts',
          action='store_true'
    )
    group = parser.add_argument_group('others')
    exclusive_group = group.add_mutually_exclusive_group()
    exclusive_group.add_argument(
                '-l',
               '--list',
            dest='print_snapshots',
            help='List all snapshots.',
          action='store_true'
    )
    exclusive_group.add_argument(
                '-r',
               '--rollback',
            dest='rollback',
            help='Withdraw a previous snapshot.',
           nargs=argparse.REMAINDER,
         metavar='OPTION'
    )
    exclusive_group.add_argument(
                '-p',
               '--pack',
            dest='pack',
            help='Pack the current system into a image.',
           nargs=argparse.REMAINDER,
         metavar='OPTION'
    )
    exclusive_group.add_argument(
                '-e',
               '--export',
            dest='export',
            help='Export the current build system.',
           nargs=argparse.REMAINDER,
         metavar='OPTION'
    )
    opts = parser.parse_args()
    if opts.print_snapshots:
        print_snapshots()
    elif opts.rollback is not None:
        parser = argparse.ArgumentParser(
                 prog=f'{parser.prog} --rollback',
                 description='withdraw a previous snapshot')
        parser.add_argument(
                   '-y',
                  '--yes',
               dest='yes',
               help='Automatic yes to prompts',
             action='store_true'
        )
        opts = parser.parse_args(opts.rollback)
        try:
            rollback(yes=opts.yes)
        except PermissionError as err:
            print (err.strerror, file=sys.stderr)
            sys.exit(errno.EACCES)
        except FileNotFoundError as err:
            print (err.strerror, file=sys.stderr)
            sys.exit(errno.ENOENT)
    elif opts.pack is not None:
        parser = argparse.ArgumentParser(
                 prog=f'{parser.prog} --pack',
                 description='pack the current system into a image')
        cwd = os.path.abspath(os.getcwd())
        default_output = os.path.join(cwd, '01-core.sb')
        parser.add_argument(
                   '-o',
                  '--output',
               dest='output',
               help=('Place the output into FILE, '
                     'the default is "./01-core.sb"'),
            metavar='FILE',
            default=default_output
        )
        parser.add_argument(
                   '-y',
                  '--yes',
               dest='yes',
               help='Automatic yes to prompts',
             action='store_true'
        )
        opts = parser.parse_args(opts.pack)
        try:
            pack(output=opts.output, yes=opts.yes)
        except PermissionError as err:
            print (err.strerror, file=sys.stderr)
            sys.exit(errno.EACCES)
        except FileExistsError as err:
            print (err.strerror, file=sys.stderr)
            sys.exit(errno.EEXIST)
        except KeyboardInterrupt:
            sys.exit(0)
    elif opts.export is not None:
        parser = argparse.ArgumentParser(
                 prog=f'{parser.prog} --export',
                 description='Export the current build system')
        cwd = os.path.abspath(os.getcwd())
        default_output = os.path.join(cwd, 'mkinitramfs')
        parser.add_argument(
                   '-o',
                  '--output',
               dest='output',
               help=('Place the output into FILE, '
                     'the default is "./mkinitramfs"'),
            metavar='FILE',
            default=default_output
        )
        opts = parser.parse_args(opts.export)
        try:
            export(output=opts.output)
        except PermissionError as err:
            print (err.strerror, file=sys.stderr)
            sys.exit(errno.EACCES)
        except FileExistsError as err:
            print (err.strerror, file=sys.stderr)
            sys.exit(errno.EEXIST)
    else:
        try:
            save(no_cleanup=opts.no_cleanup, yes=opts.yes)
        except OSError as err:
            print (err.strerror, file=sys.stderr)
            sys.exit(err.errno)
        except ValueError as err:
            print (str(err), file=sys.stderr)
            sys.exit(errno.EINVAL)

EXCLUDE = r'''^$
/$
\.wh\.\.wh\.orph/
^\.wh\.\.pwd\.lock$
^\.wh\.\.wh\.plnk/
^\.wh\.\.wh\.aufs$
^var/cache/
^var/backups/
^var/tmp/
^var/log/
^var/lib/apt/
^var/lib/dhcp/
^var/lib/systemd/
^sbin/fsck\.aufs$
^etc/resolv\.conf$
^root/\.Xauthority$
^root/\.xsession-errors$
^etc/mtab$
^etc/fstab$
^boot/
^dev/
^mnt/
^proc/
^run/
^sys/
^tmp/
^usr/bin/dir2sb$
^usr/bin/initramfs_pack$
^usr/bin/initramfs_unpack$
^usr/bin/rmsbdir$
^usr/bin/savechanges$
^usr/bin/sb$
^usr/bin/sb2dir$'''
CLEANUP = '''\
/etc/.pwd.lock
/etc/apt/sources.list~
/etc/console-setup/cached*
/etc/fstab
/etc/mtab
/etc/ssh/ssh_host*
/etc/systemd/system/timers.target.wants/
/home/*/.bash_history
/home/*/.cache/
/home/*/.local/share/klipper/
/home/*/.python_history
/home/*/.ssh/
/home/*/.sudo_as_admin_successful
/home/*/.Xauthority
/home/*/.xsession-errors
/root/.wget-hsts
/root/.bash_history
/root/.cache/
/root/.python_history
/root/.ssh/
/root/.Xauthority
/root/.xsession-errors
/var/backups/*
/var/cache/man/*/
/var/cache/apparmor/*/
/var/cache/apt/archives/*.deb
/var/cache/apt/*.bin
/var/cache/debconf/*
/var/cache/debconf/*-old
/var/cache/fontconfig/*
/var/cache/ldconfig/*
/var/lib/apt/extended_states
/var/lib/apt/lists/deb.*
/var/lib/connman/*/
/var/lib/dhcp/dhclient.leases
/var/lib/dpkg/*-old
/var/lib/systemd/random-seed
/var/log/*
/var/log/*/*
/var/log/*/*/*
/var/log/journal/*/'''

if '__main__' == __name__:
    main()
