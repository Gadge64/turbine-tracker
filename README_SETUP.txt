Wind Turbine Tracker - Windows setup

Required software:
1. Python 3.13 or newer from https://www.python.org/downloads/windows/
   IMPORTANT: tick "Add python.exe to PATH" during install.
2. A code editor, for example Notepad++.
3. A web browser such as Chrome, Edge, or Firefox.

How to run:
1. Unzip this folder somewhere easy, for example:
   C:\Users\<you>\Documents\turbine_tracker
2. Double-click run.bat.
3. Wait until it shows:
   Running on http://127.0.0.1:5000
4. Open this address in your browser:
   http://127.0.0.1:5000

Database:
- The app uses SQLite.
- The database file is created automatically at:
  instance\wind.db
- To move your old data from another PC, copy your old wind.db into this new app's instance folder.
  If the instance folder does not exist yet, run the app once first.

Editing:
- Main Python file: app.py
- Website pages: templates\*.html

Stopping the website:
- Click the command prompt window running the app.
- Press CTRL + C.
- Then close the window.

Common issue:
If "py is not recognized", install Python again and tick "Add python.exe to PATH".
