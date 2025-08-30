#!/bin/micropython

import array
import builtins
import errno
import ffi
import json
import os
import sys

def main():
    try:
        device, fields, init, home, bundles = find_data()
    except (LookupError, OSError):
        print ('Data partition not found', file=sys.stderr)
        LIBC__exit(1)
    ld_linux = sys.argv[1] if len(sys.argv) > 1 else None
    data = { 'ld_linux': ld_linux,
                 'home': home,
               'memory': '/memory',
              'datamnt': '/memory/data',
                'union': '/memory/union',
              'bundles': '/memory/bundles',
              'changes': '/memory/changes',
              'workdir': '/memory/workdir' }
    data['device'] = device
    data.update((f'device_{k}', v) for k, v in fields.items())
    with open('/memory/arguments', 'w') as f:
        f.write(json.dumps(data))
    if init:
        print (f'Execute the setup script {init}')
        if ld_linux is None:
            status = LIBC_system(f'{sys.executable} {init}')
        else:
            status = LIBC_system(f'{ld_linux} {sys.executable} {init}')
        if status != 0:
            LIBC__exit(status)
    elif home:
        if bundles is None:
            print ('Data partition not found', file=sys.stderr)
            LIBC__exit(1)
        print ('\033[0;32m* \033[0;39mMounting bundles')
        try:
            mount_sorted_bundles_and_init_union(bundles)
        except OSError as err:
            print (err.args[1], file=sys.stderr)
        initialize(home)
        print ('\033[0;32m* \033[0;39mInitialization completed\n'
               'The startup file has been generated '
               f'at "{home}/default.py"\n'
               'Please reboot your computer with Ctrl+Alt+Delete ...')
        LIBC_system('reboot -f')
        LIBC__exit(0)
    else:
        print ('Data partition not found', file=sys.stderr)
        LIBC__exit(1)

def find_data():
    print ('\033[0;32m* \033[0;39mLooking for data ..')
    kernel_arguments = dict(cmdline())
    uuid = kernel_arguments.get('fresh_os.uuid')
    name = kernel_arguments.get('fresh_os.name')
    if kernel_arguments.get('fresh_os'):
        seq = kernel_arguments['fresh_os'].split('/', 1)
        if len(seq) > 1:
            uuid = seq[0] if uuid is None else uuid
            name = seq[1] if name is None else name
    for device, fields in sorted(blkid()):
        if device.startswith('/dev/loop') \
           or 'TYPE' not in fields \
           or 'swap' == fields['TYPE'] \
           or 'UUID' not in fields \
           or (uuid and uuid != fields['UUID']):
            continue
        init = home = bundles = None
        if uuid:
            status = LIBC_system(f'/bin/mount {device} /memory/data '
                                 f'{fs_options(fields["TYPE"])}')
            if status != 0:
                raise LookupError(uuid)
            try:
                if name:
                    if isfile(f'/memory/data/{name}'):
                        init = f'/memory/data/{name}'
                        home = init.rsplit('/', 1)[0]
                        print (f'* Found on {device}')
                        return device, fields, init, home, bundles
                    elif isdir(f'/memory/data/{name}'):
                        home = f'/memory/data/{name}'
                        if isfile(f'/memory/data/{name}/default.py'):
                            init = f'/memory/data/{name}/default.py'
                        else:
                            bundles = find_sorted_bundles(home)
                            if not bundles:
                                raise LookupError(f'{uuid}/{name}')
                        print (f'* Found on {device}')
                        return device, fields, init, home, bundles
                    else:
                        raise LookupError(f'{uuid}/{name}')
                else:
                    if isfile(f'/memory/data/default.py'):
                        init = f'/memory/data/default.py'
                        print (f'* Found on {device}')
                        return device, fields, init, home, bundles
                    else:
                        raise LookupError(uuid)
            finally:
                if init is None and home is None:
                    status = LIBC_system(f'/bin/umount /memory/data')
                    if status != 0:
                        raise OSError(errno.EIO, 'Input/output error')
        else:
            status = LIBC_system(f'/bin/mount {device} /memory/data '
                                 f'{fs_options(fields["TYPE"])}')
            if status != 0:
                continue
            try:
                if name:
                    if isfile(f'/memory/data/{name}'):
                        init = f'/memory/data/{name}'
                        home = init.rsplit('/', 1)[0]
                        print (f'* Found on {device}')
                        return device, fields, init, home, bundles
                    elif isdir(f'/memory/data/{name}'):
                        home = f'/memory/data/{name}'
                        if isfile(f'/memory/data/{name}/default.py'):
                            init = f'/memory/data/{name}/default.py'
                        else:
                            bundles = find_sorted_bundles(home)
                            if not bundles:
                                continue
                        print (f'* Found on {device}')
                        return device, fields, init, home, bundles
                elif isfile(f'/memory/data/default.py'):
                    init = f'/memory/data/default.py'
                    print (f'* Found on {device}')
                    return device, fields, init, home, bundles
            finally:
                if init is None and home is None:
                    status = LIBC_system(f'/bin/umount /memory/data')
                    if status != 0:
                        raise OSError(errno.EIO, 'Input/output error')
    else:
        raise LookupError('not found')

def initialize(home):
    passwd = {}
    with open('/memory/union/etc/passwd') as f:
        for row in f.readlines():
            seq = row.split(':', 4)
            if len(seq) > 3:
                user, _, _uid, _gid = seq[0:4]
                if len(_uid) > 10 or len(_gid) > 10:
                    continue
                try:
                    uid = int(_uid)
                except (TypeError, ValueError):
                    continue
                try:
                    gid = int(_gid)
                except (TypeError, ValueError):
                    gid = None
                passwd[user] = (uid, gid)
    records, choices = [], []
    for user in os.listdir('/memory/union/home'):
        if user not in passwd or not isdir(f'/memory/union/home/{user}'):
            continue
        uid, gid = passwd[user]
        choices.append((uid, gid))
        for dn in xdg_dirs:
            if not isdir(f'/memory/union/home/{user}/{dn}'):
                continue
            makedirs(f'{home}/home/{user}/{dn}')
            if gid is None:
                LIBC_system(f'/bin/chown {uid} '
                            f'"{home}/home/{user}/{dn}"')
            else:
                LIBC_system(f'/bin/chown {uid}:{gid} '
                            f'"{home}/home/{user}/{dn}"')
            escaped = dn.replace('\t', r'\011').replace(' ', r'\040')
            records.append(f'/run/initramfs{home}/home/{user}/{escaped} '
                           f'/home/{user}/{escaped} '
                           'none bind,x-gvfs-hide 0 0')
    data = ('proc /proc proc defaults 0 0\n'
            'sysfs /sys sysfs defaults 0 0\n'
            'devpts /dev/pts devpts gid=5,mode=620 0 0\n'
            'tmpfs /dev/shm tmpfs defaults 0 0\n')
    if records:
        data += '\n'.join(records) + '\n'
    with open(f'{home}/fstab.txt', 'w') as f:
        f.write(data)
    if choices:
        fs_uid, fs_gid = min(choices)
    else:
        fs_uid, fs_gid = None, None
    if fs_gid is None:
        options = f'uid={uid},dmask=0027,fmask=0137'
    else:
        options = f'uid={uid},gid={gid},dmask=0027,fmask=0137'
    with open(f'{home}/default.py', 'w') as f:
        f.write(fstab_py_in.replace('{options}', options))

def get_arguments():
    with open('/memory/arguments') as f:
        args = json.loads(f.read())
    return args

def mount_sorted_bundles_and_init_union(bundles):
    mountpoints = []
    for bundle, mountpoint in bundles:
        try:
            os.mkdir(mountpoint)
        except OSError as err:
            if err.args[0] != errno.EEXIST:
                raise err
        if LIBC_system('/bin/mount -o loop,ro -t squashfs '
                       f'"{bundle}" "{mountpoint}"') != 0:
            raise OSError(errno.EIO, 'Failed to mount {bundle}')
        print (f'* {bundle.rsplit("/", 1)[-1]}')
        mountpoints.append(mountpoint)
    lowerdir = ':'.join(reversed(mountpoints))
    if LIBC_system(f'mount -t overlay overlay -o lowerdir={lowerdir},'
                   f'upperdir=/memory/changes,workdir=/memory/workdir'
                   ' /memory/union') != 0:
        raise OSError(errno.EIO, 'Union file system mount failed')

def find_sorted_bundles(home):
    bundles = []
    for fn in sorted(os.listdir(home)):
        if not fn.endswith('.sb') or '.sb' == fn:
            continue
        if isfile(f'{home}/{fn}'):
            base = fn.rsplit('.', 1)[0]
            bundles.append((f'{home}/{fn}', f'/memory/bundles/{base}'))
    if isdir(f'{home}/snapshots'):
        for fn in sorted(os.listdir(f'{home}/snapshots')):
            if not fn.endswith('.sb') or '.sb' == fn:
                continue
            if isfile(f'{home}/snapshots/{fn}'):
                base = fn.rsplit('.', 1)[0]
                bundles.append((f'{home}/snapshots/{fn}',
                                f'/memory/bundles/snapshots/{base}'))
    return bundles

def remount_data(args, options):
    if LIBC_system(f'/bin/umount /memory/data') != 0:
        raise OSError(errno.EIO, f'Failed to umount {args["device"]}')
    if LIBC_system(f'/bin/mount -o {options} -t {args["device_TYPE"]} '
                   f'{args["device"]} /memory/data') != 0:
        raise OSError(errno.EIO, f'Failed to mount {args["device"]}')

def install_scripts():
    if not isdir('/memory/union/usr/bin'):
        if not isdir('/memory/union/usr'):
            os.mkdir('/memory/union/usr')
        os.mkdir('/memory/union/usr/bin')
    for fn in ('dir2sb initramfs_pack initramfs_unpack rmsbdir '
               'savechanges sb sb2dir').split():
        LIBC_system(f'/bin/cp /usr/share/fresh_os/{fn} '
                    '/memory/union/usr/bin && '
                    f'chmod 0755 /memory/union/usr/bin/{fn}')

def cmdline():
    with open('/proc/cmdline') as f:
        data = f.read()
    return [(k.replace('-', '_'), v) for k, v in parse_cmdline(data)]

def blkid():
    devices = []
    f, pid = popen('/sbin/blkid')
    try:
        data = f.read()
    finally:
        f.close()
        waitpid(pid)
    for row in data.split('\n'):
        seq = row.split(':', 1)
        if 2 == len(seq):
            devices.append((
                seq[0].strip(),
                dict((k.upper(), v) for k, v in parse_cmdline(seq[1]))
            ))
    return sorted(devices)

def fs_options(typ):
    options = f'-t {typ} -o rw'
    if 'vfat' == typ:
        options += ',check=s,shortname=mixed,iocharset=utf8'
    return options

def parse_cmdline(data):
    result, key, value, step, stop = [], [], [], 0, None
    for c in data:
        if 0 == step:
            if c in ' \t\n\r\f':
                if key:
                    step = 1
            elif '=' == c:
                step = 2
            else:
                key.append(c)
        elif 1 == step:
            if '=' == c:
                step = 2
            elif c not in ' \t\n\r\f':
                result.append((''.join(key), ''))
                key, step = [c], 0
        elif 2 == step:
            if c in '\'\"':
                step, stop = 3, c
            elif c not in ' \t\n\r\f':
                value.append(c)
                step, stop = 3, None
        elif 3 == step:
            if stop is None:
                if c in ' \t\n\r\f':
                    result.append((''.join(key), ''.join(value)))
                    key, value, step = [], [], 0
                else:
                    value.append(c)
            else:
                if c == stop:
                    result.append((''.join(key), ''.join(value)))
                    key, value, step = [], [], 0
                else:
                    value.append(c)
    if step < 3:
        if key:
            result.append((''.join(key), ''))
    elif stop is None:
        result.append((''.join(key), ''.join(value)))
    return result

def popen(cmd):
    pair = array.array('i', [0, 0])
    if LIBC_pipe(pair) != 0:
        raise OSError(os.errno())
    in_, out = pair
    pid = LIBC_fork()
    if 0 == pid:
        LIBC_close(in_)
        LIBC_dup2(out, 1)
        LIBC_close(out)
        LIBC__exit(LIBC_system(cmd))
    else:
        LIBC_close(out)
        return builtins.open(in_, 'r'), pid

def waitpid(pid):
    status = array.array('i', [0])
    if -1 == LIBC_waitpid(pid, status, 0):
        raise OSError(os.errno())
    return status[0]

def makedirs(path):
    if '/' == path:
        return
    parts = path.split('/')
    if '' == parts[-1]:
        parts.pop()
    if '' == parts[0]:
        parts = [f'/{parts[1]}'] + parts[2:]
    for i in range(len(parts)):
        try:
            os.mkdir('/'.join(parts[0:1+i]))
        except OSError as e:
            if e.args[0] != errno.EEXIST:
                raise

def isdir(path):
    try:
        return bool(os.stat(path)[0] & 0o040000)
    except OSError:
        return False

def isfile(path):
    try:
        return bool(os.stat(path)[0] & 0x8000)
    except OSError:
        return False

def ismount(mountpoint):
    with open('/proc/mounts') as f:
        data = f.read()
    for row in data.strip('\n'):
        seq = row.split(None, 2)
        if len(seq) < 2:
            continue
        if mountpoint == seq[1]:
            return seq[0]

LIBC = ffi.open('libc.so.6')
LIBC__exit = LIBC.func('v', '_exit', 'i')
LIBC_close = LIBC.func("i", "close", "i")
LIBC_dup2 = LIBC.func('i', 'dup2', 'ii')
LIBC_fork = LIBC.func('i', 'fork', '')
LIBC_pipe = LIBC.func('i', 'pipe', 'p')
LIBC_system = LIBC.func('i', 'system', 's')
LIBC_waitpid = LIBC.func('i', 'waitpid', 'ipi')
fstab_py_in = '''#!/bin/micropython\n
import bootstraplib

def main():
    args = bootstraplib.get_arguments()
    if 'exfat' == args['device_TYPE']:
        options = '{options}'
        bootstraplib.remount_data(args, options)
    bundles = bootstraplib.find_sorted_bundles(args['home'])
    bootstraplib.mount_sorted_bundles_and_init_union(bundles)
    with open(f'{args["home"]}/fstab.txt') as f_in:
        with open('/memory/union/etc/fstab', 'w') as f_out:
            f_out.write(f_in.read())
    bootstraplib.install_scripts()

if '__main__' == __name__:
    main()\n'''
xdg_dirs = '''Desktop,Documents,Downloads,Music,\
Pictures,Public,Templates,Videos,VirtualBox VMs'''.split(',')

if '__main__' == __name__:
    main()
