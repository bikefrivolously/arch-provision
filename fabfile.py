from fabric import Connection, task
from invoke.exceptions import UnexpectedExit
import sys
from getpass import getpass

def check_local(c):
    if not isinstance(c, Connection) or c.host.startswith('localhost') or c.host.startswith('127.'):
        print('Refusing to run on localhost')
        sys.exit(1)

def chroot_run(c, cmd):
        cmd = "/usr/bin/arch-chroot /mnt sh -c '" + cmd + "'"
        return c.run(cmd)

def is_internet_connected(c):
    result = c.run('ping -c 3 archlinux.org')
    return result.ok

def enable_ntp(c):
    print('Enabling NTP')
    result = c.run('timedatectl set-ntp true')
    return result

def is_uefi(c):
    return bool(c.run('test -d /sys/firmware/efi/efivars', warn=True))

def partition_disk(c, device):
    if c.run('test -b {}'.format(device)):
        if is_uefi(c):
            c.run('parted --script --align optimal {} mklabel gpt mkpart efi fat32 1 512 mkpart root 512 100% set 1 esp on'.format(device))
            c.run('mkfs.fat -F32 {}1'.format(device))
        else:
            c.run('parted --script --align optimal {} mklabel gpt mkpart bios_boot 1 2 mkpart root 2 100% set 1 bios_grub on'.format(device))
        c.run('parted --script {} set 2 lvm on print all'.format(device))
        c.run('pvcreate {}2'.format(device))
        c.run('vgcreate vg-os {}2'.format(device))
        c.run('lvcreate -n lv-root -l 50%VG vg-os')
        c.run('mkfs.ext4 /dev/vg-os/lv-root')

def mount_partitions(c):
    c.run('mount /dev/vg-os/lv-root /mnt')
    if is_uefi(c):
        c.run('mkdir -p /mnt/boot/efi')
        c.run('mount /dev/sda1 /mnt/boot/efi')

def upload_mirrorlist(c, mirrorlist='/etc/pacman.d/mirrorlist'):
    c.put(mirrorlist, '/etc/pacman.d/mirrorlist')

def install_base(c, packages=['base', 'base-devel', 'grub', 'openssh', 'neovim', 'bash-completion', 'python', 'fish', 'haveged', 'termite-terminfo']):
    if 'grub' in packages and is_uefi(c):
        packages.append('efibootmgr')
    p = ' '.join(packages)

    # Try to mount oviraptor's package cache
    try:
        c.run('mount -t nfs -o timeo=100,retry=0 192.168.2.20:/packages /var/cache/pacman/pkg')
    except UnexpectedExit as e:
        print('Error mounting package cache:', e)
        c.run('pacstrap /mnt {}'.format(p))
    else:
        c.run('pacstrap -c /mnt {}'.format(p))

def patch_arch_chroot(c):
    # this fixes https://bugs.archlinux.org/task/61040
    # is included in arch-install-scripts-21-1 (in Testing repo as of 2019-01-05)
    c.put('arch-chroot-lvm-grub.patch')
    c.run('pacman -Sy --noconfirm patch')
    with c.cd('/usr/bin'):
        c.run('patch -i /root/arch-chroot-lvm-grub.patch')

def create_fstab(c):
    c.run('genfstab -U /mnt >> /mnt/etc/fstab')
    c.run('cat /mnt/etc/fstab')

def set_timezone(c):
    chroot_run(c, 'ln -sf /usr/share/zoneinfo/America/Toronto /etc/localtime')
    chroot_run(c, 'hwclock --systohc')

def set_locale(c):
    chroot_run(c, 'sed -i "s/#en_CA.UTF-8/en_CA.UTF-8/" /etc/locale.gen')
    chroot_run(c, 'locale-gen')
    chroot_run(c, 'echo "LANG=en_CA.UTF-8" >> /etc/locale.conf')

def set_hostname(c, hostname):
    chroot_run(c, 'echo {} > /etc/hostname'.format(hostname))
    chroot_run(c, 'echo "127.0.0.1\tlocalhost" >> /etc/hosts')
    chroot_run(c, 'echo "::1\tlocalhost" >> /etc/hosts')
    chroot_run(c, 'echo "127.0.1.1\t{}.localdomain\t{}" >> /etc/hosts'.format(hostname, hostname))

def enable_dhcp(c):
    c.put('dhcp.network', '/mnt/etc/systemd/network/dhcp.network')
    chroot_run(c, 'systemctl enable systemd-networkd.service')
    c.run('ln -sf /run/systemd/resolve/resolv.conf /mnt/etc/resolv.conf')
    chroot_run(c, 'systemctl enable systemd-resolved.service')

def enable_root_ssh(c):
    c.run('sed -i "s/^#PermitRootLogin .*$/PermitRootLogin yes/" /mnt/etc/ssh/sshd_config')
    chroot_run(c, 'systemctl enable sshd.service')

def enable_haveged(c):
    chroot_run(c, 'systemctl enable haveged.service')

def make_initramfs(c):
    c.run('sed -i.bak "s/^HOOKS=.*block/& lvm2/" /mnt/etc/mkinitcpio.conf')
    chroot_run(c, 'mkinitcpio -p linux')

def set_root_password(c):
    passwd = c.connect_kwargs['password']
    chroot_run(c, 'echo "root:{}" | chpasswd'.format(passwd))

def install_grub(c):
    if is_uefi(c):
        chroot_run(c, 'grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=GRUB')
    else:
        chroot_run(c, 'grub-install --target=i386-pc /dev/sda')

    chroot_run(c, 'grub-mkconfig -o /boot/grub/grub.cfg')

def setup_sudo(c):
    c.put('10-wheel', '/mnt/etc/sudoers.d/')

def create_skel(c):
    c.run('mkdir -p /mnt/etc/skel/.config/nvim')
    c.put('init.vim', '/mnt/etc/skel/.config/nvim/')

def install_keys(c):
    c.run('mkdir /mnt/root/.ssh')
    c.run('chmod 700 /mnt/root/.ssh')
    c.put('authorized_keys', '/mnt/root/.ssh/')

def post_install_cleanup(c):
    c.run('umount -R /mnt', warn=True)
    try:
        c.run('mount | grep -q "/var/cache/pacman/pkg"')
    except UnexpectedExit:
        pass
    else:
        c.run('umount /var/cache/pacman/pkg')

@task
def provision(c, hostname):
    check_local(c)
    print('''About to provision {}.
This is a destructive operation.'''.format(c.host))
    i = input('Are you sure? (y/N) ')
    if i.lower() not in ('y', 'yes'):
        return
    
    print('Checking internet connectivity...', end='')
    if is_internet_connected(c):
        print('OK')
        enable_ntp(c)
        partition_disk(c, '/dev/sda')
        mount_partitions(c)
        upload_mirrorlist(c)
        install_base(c)
        #patch_arch_chroot(c)
        create_fstab(c)
        set_timezone(c)
        set_locale(c)
        set_hostname(c, hostname)
        enable_dhcp(c)
        enable_root_ssh(c)
        enable_haveged(c)
        make_initramfs(c)
        set_root_password(c)
        install_grub(c)
        setup_sudo(c)
        install_keys(c)
        create_skel(c)
        post_install_cleanup(c)

@task
def provision_laptop(c, hostname):
    check_local(c)
    print('''About to provision {}.
This is a destructive operation.'''.format(c.host))
    i = input('Are you sure? (y/N) ')
    if i.lower() not in ('y', 'yes'):
        return

    print('Checking internet connectivity...', end='')
    if is_internet_connected(c):
        print('OK')
        enable_ntp(c)
        #partition_disk(c, '/dev/sda')
        #mount_partitions(c)
        upload_mirrorlist(c)
        install_base(c)
        create_fstab(c)
        set_timezone(c)
        set_locale(c)
        set_hostname(c, hostname)
        enable_dhcp(c)
        enable_root_ssh(c)
        make_initramfs(c)
        set_root_password(c)
        #install_grub(c)
        setup_sudo(c)
        install_keys(c)
        create_skel(c)
        post_install_cleanup(c)

@task
def setup_user(c, username, admin=True):
    '''Create a new user. Must run after rebooting into the new OS'''
    check_local(c)
    password = getpass(prompt='New passford for {}: '.format(username))
    c.run('useradd --user-group --create-home --shell /usr/bin/fish {}'.format(username))
    c.run('echo {}:{} | chpasswd'.format(username, password))
    c.run('mkdir -p /home/{}/.ssh'.format(username))
    c.put('authorized_keys', '/home/{}/.ssh/'.format(username))
    c.run('chmod 700 /home/{}/.ssh'.format(username))
    c.run('chmod 600 /home/{}/.ssh/authorized_keys'.format(username))
    c.run('chown -R {}:{} /home/{}/.ssh'.format(username, username, username))
    if admin:
        c.run('usermod --append --groups wheel {}'.format(username))

@task
def test_task(c):
    check_local(c)
    print(c.config)
    r = None
    try:
        c.run('mount | grep -q "/var/cache/pacman/pkg"')
    except UnexpectedExit:
        pass
    else:
        c.run('umount /var/cache/pacman/pkg')

    print(r)
