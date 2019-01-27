# arch-provision

Uses fabric (https://www.fabfile.org/) to set up an Arch Linux system over SSH.
This is not fool proof. Review and modify the files as needed for your purposes. In particular, put your SSH public key in authorized_keys.

Boot from the Arch ISO, then:

Set a root password:
passwd

Enable sshd:
systemctl start sshd

Check your IP address:
ip addr

Then, on the system with arch-provision:
fab --prompt-for-login-password root@<IP> provision --hostname=<new_host_name>

Reboot into the new system. The root password is the same as what you set from the ISO.

If desired, run from the provisioning system:
fab root@<IP> setup-user -u <USER>
