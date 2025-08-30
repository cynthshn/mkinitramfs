#!/usr/bin/python3

import errno
import glob
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

def build(output):
    if os.path.exists(output):
        raise FileExistsError(errno.EEXIST, f'File "{output}" exists')
    static = os.path.abspath(os.path.join(
             os.path.dirname(__file__), 'assets'))
    temp_dir_holder = smart_temporary_directory()
    temp_dir = next(temp_dir_holder).rstrip('/')
    for dn in ('bin dev etc/modprobe.d mnt proc root run sys tmp '
               'var/log usr/lib').split():
        os.makedirs(f'{temp_dir}/{dn}', exist_ok=True)
    os.symlink('bin', f'{temp_dir}/sbin')
    for fn in ('blkid busybox eject micropython').split():
        dst = f'{temp_dir}/bin/{fn}'
        shutil.copy(os.path.join(static, fn), dst)
        os.chmod(dst, os.stat(dst).st_mode | 0o755)
    with open(f'{temp_dir}/etc/modprobe.d/local-loop.conf', 'w') as f:
        f.write('options loop max_loop=32')
    begin = False
    for row in run(f'{temp_dir}/bin/busybox').stdout.split(b'\n'):
        if begin:
            row = row.strip()
            if b'' == row:
                break
            for func in row.split(b','):
                func = func.decode().strip()
                if func:
                    if not os.path.exists(f'{temp_dir}/bin/{func}'):
                        os.symlink('busybox', f'{temp_dir}/bin/{func}')
        elif b'currently defined functions:' == row.strip().lower():
            begin = True
    for mode, major, minor, fn in \
        [(stat.S_IFCHR | 0o600, 5, 1, 'dev/console'),
         (stat.S_IFCHR | 0o666, 1, 3, 'dev/null'),
         (stat.S_IFBLK | 0o660, 1, 0, 'dev/ram0'),
         (stat.S_IFCHR | 0o620, 4, 1, 'dev/tty1'),
         (stat.S_IFCHR | 0o620, 4, 2, 'dev/tty2'),
         (stat.S_IFCHR | 0o620, 4, 3, 'dev/tty3'),
         (stat.S_IFCHR | 0o620, 4, 4, 'dev/tty4')]:
        os.mknod(f'{temp_dir}/{fn}', mode, os.makedev(major, minor))
    dependency = Dependency()
    dependency.collect(os.path.join(static, 'micropython'), False)
    for driver in DRIVERS:
        dependency.collect(driver)
    including_deps = sorted(dependency.including_deps)
    for src in including_deps:
        dst = f'{temp_dir}/{src.lstrip("/")}'
        if os.path.exists(src) and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(src, dst)
    for src, dst in dependency.symbolic_links:
        dst = f'{temp_dir}/{dst.lstrip("/")}'
        if not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.symlink(src, dst)
    for pattern, args in [('*.ko.gz', ['/usr/bin/gunzip']),
                          ('*.ko.xz', ['/usr/bin/xz', '-d'])]:
        for fn in glob.glob(f'{temp_dir}/**/{pattern}', recursive=True):
            subprocess.run(args + [fn], check=True)
    moduleorder = []
    with subprocess.Popen(['/bin/find', '-name', '*.ko'],
                          cwd=os.path.join(temp_dir, LMK),
                          stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as proc:
        stdout, _ = proc.communicate()
        for _mod in stdout.split(b'\n'):
            _mod = _mod.strip()
            if b'' == _mod:
                continue
            mod = _mod.decode()
            if mod.startswith('./'):
                moduleorder.append(mod[2:])
            elif mod.startswith('/'):
                moduleorder.append(mod[1:])
    with open(os.path.join(temp_dir, LMK, 'modules.order'), 'w') as f:
        f.write('\n'.join(reversed(moduleorder)))
        f.write('\n')
    run(f'/sbin/depmod -b {temp_dir} {os.uname().release}')
    with open(f'{temp_dir}/etc/passwd', 'w') as f:
        f.write('root::0:0::/root:/bin/sh')
    for fn in ['etc/fstab', 'etc/mtab']:
        with open(f'{temp_dir}/{fn}', 'w') as f:
            pass
    pattern = os.path.join(dst, '**/__pycache__')
    for dn in glob.glob(pattern, recursive=True):
        if os.path.isdir(dn):
            shutil.rmtree(dn)
    ld_linux = get_ld_linux()
    with open(os.path.join(static, 'init.in')) as file_in:
        with open(f'{temp_dir}/init', 'w') as file_out:
            file_out.write(file_in.read().replace('{run_bootstrap_py}',
                     f'{ld_linux} /bin/micropython /bin/bootstrap.py '
                     f'{ld_linux}'))
    shutil.copy(os.path.join(static, 'shutdown'), f'{temp_dir}/shutdown')
    os.makedirs(f'{temp_dir}/usr/lib/micropython', exist_ok=True)
    shutil.copy(
        os.path.abspath(os.path.join(
        os.path.dirname(__file__), 'bootstraplib.py')),
        f'{temp_dir}/usr/lib/micropython/bootstraplib.py'
    )
    with open(f'{temp_dir}/bin/bootstrap.py', 'w') as f:
        f.write('#!/bin/micropython\n\nimport bootstraplib\n\n'
                'if "__main__" == __name__:\n    bootstraplib.main()\n')
    os.makedirs(f'{temp_dir}/usr/share/fresh_os', exist_ok=True)
    shutil.copy(
        os.path.abspath(os.path.join(
        os.path.dirname(__file__), 'savechanges.py')),
        f'{temp_dir}/usr/share/fresh_os/savechanges'
    )
    shutil.copy(
        os.path.abspath(__file__),
        f'{temp_dir}/usr/share/fresh_os/initramfs_create.py'
    )
    for fn in ('dir2sb initramfs_pack init.in initramfs_unpack '
               'rmsbdir sb sb2dir').split():
        shutil.copy(os.path.join(static, fn),
                    f'{temp_dir}/usr/share/fresh_os/{fn}')
    for dst in [ld_linux,
                f'{temp_dir}/init',
                f'{temp_dir}/shutdown',
                f'{temp_dir}/bin/bootstrap.py',
                f'{temp_dir}/usr/share/fresh_os/savechanges',
                f'{temp_dir}/usr/share/fresh_os/dir2sb',
                f'{temp_dir}/usr/share/fresh_os/initramfs_create.py',
                f'{temp_dir}/usr/share/fresh_os/initramfs_pack',
                f'{temp_dir}/usr/share/fresh_os/initramfs_unpack',
                f'{temp_dir}/usr/share/fresh_os/rmsbdir',
                f'{temp_dir}/usr/share/fresh_os/sb',
                f'{temp_dir}/usr/share/fresh_os/sb2dir']:
        os.chmod(dst, os.stat(dst).st_mode | 0o755)
    with open(output, 'wb') as f:
        find_proc = subprocess.Popen(['/bin/find', '.', '-print'],
                    cwd=temp_dir,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        args = ['/usr/bin/cpio', '-o', '-H', 'newc']
        cpio_proc = subprocess.Popen(args,
                    cwd=temp_dir, stdin=find_proc.stdout,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        args = ['/usr/bin/xz', '-T0', '-f', '--extreme', '--check=crc32']
        xz_proc = subprocess.Popen(args, stdin=cpio_proc.stdout,
                  stdout=f, stderr=subprocess.DEVNULL)
        find_proc.stdout.close()
        cpio_proc.stdout.close()
        xz_proc.communicate()

class Dependency:

    regex_ldd_dep = re.compile(rb'^\s*'
                    rb'(?:((?:\\.|(?<!=)>|[^>\s])+)\s*=>\s*)?'
                    rb'((?:\\.|[^\s(])+)'
                    rb'(?:\s*\([^)]*\)|\s+.*)'       rb'\s*$')
    regex_so_ext = re.compile(r'.*(\.so(\.\d+)*)$')
    regex_modules_dep_row = re.compile(
                            r'^\s*((?:\\.|[^:])+)\s*:\s*(.*)\s*$')
    regex_modules_dep_dep = re.compile(r'(?:^|(?<=\s))(?:\\.|[^\\\s])+')

    def __init__(self):
        self.including_deps = set()
        self.symbolic_links = set()
        self.modules_dep = {}
        with open(f'/{LMK}/modules.dep') as f:
            for row in f.readlines():
                match = Dependency.regex_modules_dep_row.match(row)
                if match:
                    key, deps = match.groups()
                    self.modules_dep[key] = \
                         Dependency.regex_modules_dep_dep.findall(deps)

    def collect(self, mod, include_source=True):
        _mod = os.path.abspath(mod)
        if os.path.isdir(_mod):
            pattern = os.path.join(_mod, '**/*.ko')
            for kernel_object in glob.glob(pattern, recursive=True):
                self.include_kernel_object(kernel_object)
        elif os.path.isfile(_mod):
            if _mod.endswith('.ko'):
                self.include_kernel_object(_mod)
            else:
                self.include_shared_object(_mod)
            if include_source:
                self.include_file(_mod)
        elif include_source:
            if os.path.exists(_mod):
                self.include_file(_mod)

    def include_kernel_object(self, kernel_object):
        if kernel_object.startswith(f'/{LMK}/'):
            lmk_len = len(f'/{LMK}/')
            key = kernel_object[lmk_len:]
            if key in self.modules_dep:
                for dep in self.modules_dep[key]:
                    self.include_kernel_object(f'/{LMK}/{dep}')
            self.including_deps.add(kernel_object)

    def include_shared_object(self, shared_object):
        proc = subprocess.run(['/usr/bin/ldd', shared_object],
                              capture_output=True)
        if 0 == proc.returncode:
            for row in proc.stdout.split(b'\n'):
                match = Dependency.regex_ldd_dep.match(row)
                if match is None:
                    continue
                _obj, _loc = match.groups()
                if not _loc.startswith(b'/'):
                    continue
                loc = os.path.realpath(_loc.decode())
                if _obj is not None:
                    obj = _obj.decode()
                    src = os.path.basename(loc)
                    if src != obj:
                        dst = os.path.join(os.path.dirname(loc), obj)
                        self.symbolic_links.add((src, dst))
                self.including_deps.add(loc)

    def include_file(self, mod):
        if mod.startswith(f'/{LMK}/'):
            self.including_deps.add(mod)
        else:
            loc = os.path.realpath(mod)
            if mod == loc:
                self.including_deps.add(mod)
            else:
                if os.path.dirname(loc) == os.path.dirname(mod):
                    self.symbolic_links.add((os.path.basename(loc), mod))
                else:
                    self.symbolic_links.add((loc, mod))
                self.including_deps.add(loc)

def wildcard(pattern):
    pathes = []
    for row in pattern.split('\n'):
        row = row.lstrip()
        if not row or '#' == row[0]:
            continue
        if '*' in row:
            pathes.extend(glob.glob(row, recursive=True))
        else:
            pathes.append(row)
    return sorted(pathes)

def get_ld_linux():
    for row in run(f'/usr/bin/ldd {sys.executable}').stdout.split(b'\n'):
        match = Dependency.regex_ldd_dep.match(row)
        if match:
            shared_object = match.groups()[1]
            if b'/ld-linux' in shared_object:
                return os.path.realpath(shared_object.decode())

def run(cmd):
    return subprocess.run(
           cmd, capture_output=True, shell=True, check=True)

def smart_temporary_directory():
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir

def main():

    import argparse

    parser = argparse.ArgumentParser()
    cwd = os.path.abspath(os.getcwd())
    default_output = os.path.join(cwd,
                     f'initrfs-{os.uname().release}.img')
    parser.add_argument(
               '-o',
              '--output',
           dest='output',
           help='Place the output into FILE',
        metavar='FILE',
        default=default_output
    )
    opts = parser.parse_args()
    if os.geteuid() != 0:
        program = os.path.basename(sys.argv[0])
        print (f'only root can run "{program}"', file=sys.stderr)
        sys.exit(errno.EACCES)
    try:
        build(output=opts.output)
    except NotADirectoryError as e:
        print (e.strerror, file=sys.stderr)
        sys.exit(errno.ENOTDIR)
    except FileExistsError as e:
        print (e.strerror, file=sys.stderr)
        sys.exit(errno.EEXIST)
    except LookupError as e:
        print (str(e), file=sys.stderr)
        sys.exit(errno.ENOENT)
    except ValueError as e:
        print (str(e), file=sys.stderr)
        sys.exit(errno.EINVAL)
    except KeyboardInterrupt:
        sys.exit(0)

LMK = f'lib/modules/{os.uname().release}'
DRIVERS = wildcard(f'''\
/usr/bin/strace
/usr/bin/lsof
/{LMK}/kernel/fs/aufs
/{LMK}/kernel/fs/exfat
/{LMK}/kernel/fs/ext2
/{LMK}/kernel/fs/ext3
/{LMK}/kernel/fs/ext4
/{LMK}/kernel/fs/f2fs
/{LMK}/kernel/fs/fat
/{LMK}/kernel/fs/fuse
/{LMK}/kernel/fs/isofs
/{LMK}/kernel/fs/nls
/{LMK}/kernel/fs/ntfs
/{LMK}/kernel/fs/ntfs3
/{LMK}/kernel/fs/overlayfs
/{LMK}/kernel/fs/reiserfs
/{LMK}/kernel/fs/squashfs
# crc32c is needed for ext4, but I don't know which one,
# add them all, they are small
/{LMK}/kernel/**/*crc32c*
# needed by zr
/{LMK}/kernel/drivers/staging/zsmalloc
/{LMK}/kernel/drivers/block/zram
/{LMK}/kernel/drivers/block/loop.*
# usb drivers
/{LMK}/kernel/drivers/usb/common
/{LMK}/kernel/drivers/usb/core
/{LMK}/kernel/drivers/usb/host
/{LMK}/kernel/drivers/hid/usbhid
/{LMK}/kernel/drivers/hid/hid.*
/{LMK}/kernel/drivers/hid/uhid.*
/{LMK}/kernel/drivers/hid/hid-generic.*
/{LMK}/kernel/drivers/usb/storage
# disk and cdrom drivers
/{LMK}/kernel/drivers/ata
/{LMK}/kernel/drivers/cdrom
/{LMK}/kernel/drivers/mmc
/{LMK}/kernel/drivers/nvme
/{LMK}/kernel/drivers/scsi/scsi_mod.*
/{LMK}/kernel/drivers/scsi/sd_mod.*
/{LMK}/kernel/drivers/scsi/sg.*
/{LMK}/kernel/drivers/scsi/sr_mod.*
# copy all custom-built modules
#/{LMK}/updates  # some drivers may cause boot failure
/{LMK}/modules.*
/usr/share/terminfo/l/linux''')

if '__main__' == __name__:
    main()
