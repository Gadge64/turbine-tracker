TURBINE TRACKER - FULL PROJECT WITH BACKUPS

1. Install Python 3.13+ from python.org.
   Tick "Add python.exe to PATH" during install.

2. Unzip this folder.

3. Copy your real database into:
   instance/wind.db

4. Double-click:
   run.bat

5. Open:
   http://127.0.0.1:5000

BACKUPS
-------

Automatic daily backups are created in:
instance/backups/

Manual full database backup download is available after login:
Download Backup

Admin backup list:
http://127.0.0.1:5000/admin/backups

RECOVERY ADMIN
--------------

If locked out, close the server and double-click:
run_recovery.bat

Login:
username: recovery_admin
password: TempReset123!

Then open:
http://127.0.0.1:5000/admin/users
