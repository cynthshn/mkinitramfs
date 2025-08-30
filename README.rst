mkinitramfs
===========

This project is a Python implementation of `Tomas Matejicek`_'s `Linux Live Kit`_ project.

There are two scripts in this project, called `mkinitramfs.py` and `savechanges.py`, which do not need to be installed, just download and run them directly.

.. code-block::

    git clone https://github.com/cynthshn/mkinitramfs.git


mkinitramfs.py
--------------

The `mkinitramfs.py` script is used to create the `initrfs.img` file.

.. code-block::

    usage: initramfs_create.py [-h] [-o FILE]

    options:
    -h, --help            show this help message and exit
    -o FILE, --output FILE
                            Place the output into FILE

Then copy the kernel file `vmlinuz` and the `initrfs.img` file to the specified location, and edit the `grub.cfg` file.

.. code-block::

    search --no-floppy --fs-uuid --set=root INITRAMFS-STORAGE-DISK-UUID
    menuentry 'NAME' {
            linux /PATH/TO/vmlinuz load_ramdisk=1 prompt_ramdisk=0 rw fresh_os=BUNDLES-STORAGE-DISK-UUID/PATH/TO/BUNDLES/DIRECTORY
            initrd /PATH/TO/initrfs.img
    }

savechanges.py
--------------
To boot the system, you also need to create a system image file with the suffix `.sb` which also known as `BUNDLE` files from the current system using the `savechanges.py` script.

.. code-block::

    usage: savechanges --pack [-h] [-o FILE] [-y]

    pack the current system into a image

    options:
    -h, --help            show this help message and exit
    -o FILE, --output FILE
                            Place the output into FILE, the default is "./01-core.sb"
    -y, --yes             Automatic yes to prompts

The system will run in live mode after booting, if you need to keep changes in the system, run the `savechanges.py` script without parameter to generate a snapshot of the current system. It is also possible to use the `--rollback` parameter to roll back the system to the previous snapshot.

.. code-block::

    usage: savechanges [-h] [--no-cleanup] [-y] [-l | -r ...]

    options:
    -h, --help            show this help message and exit

    default:
    Create a snapshot of the current system. This is the default behavior.

    --no-cleanup          Do not perform cleanup
    -y, --yes             Automatic yes to prompts

    others:
    -l, --list            List all snapshots.
    -r ..., --rollback ...
                            Withdraw a previous snapshot.

.. _Tomas Matejicek: https://github.com/Tomas-M
.. _Linux Live Kit: https://www.linux-live.org/