The files here are various system-level config files.  

Tips for the service files:  
1. Most of them are intended to be installed as user services by placing them in ~/.config/systemd/user/
1. `systemctl --user enable servicename` to enable
1. `journalctl --user-unit servicename` to check on them
